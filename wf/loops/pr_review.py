"""PR 审查 loop，分三段跑在不同 job 里：

  discover   （App 令牌）列出待审 PR，生成沙箱 matrix 和 pr-queue.json
  [sandbox]  （无任何凭证）在 workflow YAML 里实际拉代码运行，产出 sandbox-<key>/exit_code、log.txt
  review     （App 令牌 + 模型密钥）读 diff + 沙箱结果 → 模型审查 → policy → 执行

python -m wf.loops.pr_review discover --out out
python -m wf.loops.pr_review review --queue out/pr-queue.json --sandbox sandbox
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .. import markers, prompts, schemas
from ..changes import matrix_entry, select_checks, sensitive
from ..llm import LLM, LLMError, LLMOutputError
from ..policy import LABELS, ReviewContext, apply_plan, blacklisted_plan, needs_human_plan, review_plan
from ..state import Recorder
from .common import Ctx, is_maintainer, label_names, log, read_json, set_output, setup, step_summary, write_json

LOG_TAIL = 6000
BAD_CI = {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}


# ------------------------------------------------------------------ discover
def discover(ctx: Ctx, out: Path):
    cfg, gh, bot = ctx.cfg, ctx.gh, ctx.rt.bot_login
    limit = cfg.policy["pr"]["max_per_run"]
    queue, matrix = [], []
    gh.ensure_labels(cfg.control_repo, LABELS)
    for repo in cfg.repos.values():
        gh.ensure_labels(repo.full_name, LABELS)
        for pr in gh.list_open_pulls(repo.full_name):
            if len(queue) >= limit:
                break
            if pr.get("draft") or "bot:ignore" in label_names(pr):
                continue
            sha, n, author = pr["head"]["sha"], pr["number"], pr["user"]["login"]
            mark = markers.latest_bot_marker(gh.list_comments(repo.full_name, n), bot, "review")
            if mark and mark[0].get("sha") == sha:
                continue
            item = {"repo": repo.name, "number": n, "sha": sha, "author": author,
                    "blacklisted": author.lower() in cfg.blacklist and not is_maintainer(cfg, author),
                    "files": [], "checks": []}
            if not item["blacklisted"]:
                item["files"] = gh.pull_files(repo.full_name, n)
                checks = select_checks(repo, item["files"])
                item["checks"] = [c.id for c in checks]
                matrix += [matrix_entry(repo, n, sha, c) for c in checks]
            queue.append(item)
    write_json(out / "pr-queue.json", queue)
    set_output("matrix", matrix)
    set_output("has_work", "true" if queue else "false")
    set_output("has_sandbox", "true" if matrix else "false")
    step_summary([f"## PR discover：{len(queue)} 个待审，{len(matrix)} 个沙箱任务"]
                 + [f"- {q['repo']}#{q['number']} {q['sha'][:7]} checks={q['checks']}"
                    + (" **黑名单**" if q["blacklisted"] else "") for q in queue])


# ------------------------------------------------------------------ review
def sandbox_results(sandbox_dir: Path, item: dict) -> tuple[list[dict], bool]:
    """读取沙箱产物。返回 (每个 check 的结果, head 是否已变化)。产物是不可信数据，只读文本。"""
    results, moved = [], False
    for check in item["checks"]:
        d = sandbox_dir / f"sandbox-{item['repo']}-{item['number']}-{check}"
        code_file, log_file, sha_file = d / "exit_code", d / "log.txt", d / "sha"
        if sha_file.exists() and sha_file.read_text().strip() not in (item["sha"], ""):
            moved = True
        code = code_file.read_text().strip() if code_file.exists() else None
        log_text = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
        results.append({"check": check, "exit_code": code, "log_tail": log_text[-LOG_TAIL:]})
    return results, moved


def verification(results: list[dict], ci: list[dict]) -> tuple[bool, str]:
    rows = ["| 检查 | 结果 |", "|---|---|"]
    ok = True
    if not results:
        rows.append("| （无适用的沙箱检查） | — |")
    for r in results:
        if r["exit_code"] == "0":
            rows.append(f"| 沙箱 `{r['check']}` | ✅ 通过 |")
        elif r["exit_code"] is None:
            ok = False
            rows.append(f"| 沙箱 `{r['check']}` | ⚠️ 没有拿到结果 |")
        else:
            ok = False
            rows.append(f"| 沙箱 `{r['check']}` | ❌ 失败（exit {r['exit_code'][:6]}） |")
    for c in ci:
        if c["status"] != "completed":
            rows.append(f"| 仓库 CI `{c['name']}` | ⏳ {c['status']} |")
        elif c["conclusion"] in BAD_CI:
            ok = False
            rows.append(f"| 仓库 CI `{c['name']}` | ❌ {c['conclusion']} |")
        else:
            rows.append(f"| 仓库 CI `{c['name']}` | ✅ {c['conclusion']} |")
    return ok, "\n".join(rows)


def build_user_prompt(pr: dict, files: list[str], diff: str, results: list[dict], ci: list[dict],
                      repo_desc: str, has_tests: bool) -> str:
    meta = (f"仓库：{pr['base']['repo']['full_name']}（{repo_desc or '无简介'}），仓库{'有' if has_tests else '没有'}测试\n"
            f"PR #{pr['number']}，作者 {pr['user']['login']}，{pr['head']['label']} → {pr['base']['ref']}\n"
            f"改动 {len(files)} 个文件，+{pr.get('additions', '?')} -{pr.get('deletions', '?')}")
    desc = f"标题：{pr['title']}\n\n{pr.get('body') or ''}"
    runs = "\n\n".join(f"### 沙箱 {r['check']}：exit={r['exit_code']}\n{r['log_tail']}" for r in results) or "无适用检查"
    ci_text = "\n".join(f"- {c['name']}: {c['status']}/{c['conclusion']}" for c in ci) or "无"
    return "\n\n".join([
        meta,
        prompts.untrusted("pr-description", desc, 8000),
        "改动文件：\n" + "\n".join(files[:300]),
        prompts.untrusted("diff", diff, 10 ** 9),
        prompts.untrusted("sandbox-logs", runs, 30000),
        "仓库 CI：\n" + ci_text,
    ])


def review(ctx: Ctx, llm: LLM | None, queue_file: Path, sandbox_dir: Path) -> list[dict]:
    cfg, gh, bot = ctx.cfg, ctx.gh, ctx.rt.bot_login
    pp = cfg.policy["pr"]
    system = prompts.system("pr-review.md")
    rows = []
    for item in read_json(queue_file, []):
        repo = cfg.repo(item["repo"])
        n, ref = item["number"], f"{item['repo']}#{item['number']}"
        pr = gh.get_pull(repo.full_name, n)
        if pr["state"] != "open" or pr["head"]["sha"] != item["sha"]:
            log.info("%s 已关闭或有新提交，下轮再审", ref)
            continue
        labels = label_names(pr)
        if item["blacklisted"]:
            plan = blacklisted_plan(item["sha"], cfg.replies)
        else:
            results, moved = sandbox_results(sandbox_dir, item)
            if moved:
                continue
            ci = gh.check_runs(repo.full_name, item["sha"])
            ok, vmd = verification(results, ci)
            diff = gh.pull_diff(repo.full_name, n)
            author = item["author"]
            rctx = ReviewContext(
                repo=repo.name, number=n, sha=item["sha"], author=author,
                is_maintainer=is_maintainer(cfg, author), is_bot_pr=author.lower() == bot.lower(),
                verification_ok=ok, verification_md=vmd, has_tests=repo.has_tests,
                sensitive_files=sensitive(item["files"], pp["sensitive_prefixes"]),
            )
            if len(diff) > pp["max_diff_chars"]:
                plan = needs_human_plan(item["sha"], f"diff 有 {len(diff)} 字符，超过自动审查上限，需要人工审查。\n\n{vmd}",
                                        cfg.replies)
            elif llm is None:
                continue
            else:
                desc = (gh.get(f"/repos/{repo.full_name}") or {}).get("description", "")
                try:
                    result = llm.chat_json(system, build_user_prompt(pr, item["files"], diff, results, ci, desc,
                                                                     repo.has_tests), schemas.PR_REVIEW)
                    plan = review_plan(result, rctx, policy=cfg.policy, replies=cfg.replies)
                except LLMOutputError as e:
                    plan = needs_human_plan(item["sha"], f"模型两次输出都不合法（{e}），需要人工审查。\n\n{vmd}", cfg.replies)
                except LLMError as e:
                    log.error("%s 模型调用失败，本轮跳过: %s", ref, e)
                    continue
        apply_plan(gh, repo.full_name, n, plan, current_labels=labels, is_pr=True)
        if plan.violation:
            ctx.rec.violation(item["author"], repo.name, n, plan.violation)
        if plan.lesson:
            ctx.rec.lesson(plan.lesson[0], plan.lesson[1], ref)
        ctx.rec.event(repo.name, f"pr:{plan.outcome}", ref, item["sha"][:7])
        rows.append({"ref": ref, "outcome": plan.outcome, "author": item["author"]})
    return rows


def propose_blacklist(ctx: Ctx, state: dict, logins: set[str]):
    cfg, gh = ctx.cfg, ctx.gh
    threshold = cfg.policy["blacklist"]["violation_threshold"]
    open_props = set()
    for it in gh.list_open_items(cfg.control_repo, "bot:awaiting-approval"):
        kv = markers.find(it.get("body", ""), "await") if markers.is_bot(it, ctx.rt.bot_login) else None
        if kv and kv.get("action") == "blacklist":
            open_props.add(kv.get("user", "").lower())
    for login in logins:
        vs = state.get("violations", {}).get(login, [])
        if len(vs) < threshold or login in open_props or login in cfg.blacklist:
            continue
        lines = "\n".join(f"- {v['repo']}#{v['pr']}（{v['ts'][:10]}）：{v['reason']}" for v in vs)
        body = (f"账号 `{login}` 已有 {len(vs)} 个 PR 因低质量 / 可疑被关闭（阈值 {threshold}）：\n\n{lines}\n\n"
                "维护者评论 `/approve` 写入 `config/blacklist.yml`，之后该账号的 PR 会被直接关闭；`/reject` 不拉黑。\n\n"
                + markers.make("await", action="blacklist", user=login))
        gh.create_issue(cfg.control_repo, f"[拉黑建议] {login}", body, ["bot:awaiting-approval"])
        ctx.rec.event("workflow", "blacklist:proposed", login)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--out", default="out")
    r = sub.add_parser("review")
    r.add_argument("--queue", default="out/pr-queue.json")
    r.add_argument("--sandbox", default="sandbox")
    a = ap.parse_args()
    ctx = setup(f"pr-{a.cmd}")
    if a.cmd == "discover":
        discover(ctx, Path(a.out))
        return
    rows = review(ctx, LLM.from_config(ctx.cfg.bot), Path(a.queue), Path(a.sandbox))
    violators = {v[0] for v in ctx.rec.violations}
    state = ctx.rec.flush(ctx.store)
    if state is not None and violators:
        ctx.rec = Recorder("pr-review")
        propose_blacklist(ctx, state, violators)
        ctx.rec.flush(ctx.store)
    step_summary([f"## PR 审查（dry_run={ctx.rt.dry_run}）", "", "| PR | 结论 | 作者 |", "|---|---|---|"]
                 + [f"| {x['ref']} | {x['outcome']} | {x['author']} |" for x in rows])


if __name__ == "__main__":
    main()
