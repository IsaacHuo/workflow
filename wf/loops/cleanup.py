"""定期清理 loop：体检最近合并的代码 → 可安全重构的开清理 PR，可疑 / 恶意的开告警 issue，
经验教训累积后以 PR 形式写进 lessons/LESSONS.md（都要维护者 /approve）。

  prepare   （App 令牌）读游标，生成 matrix
  analyze   （模型密钥，无 GitHub 令牌）模型体检，若有可修项则生成 opencode 提示词
  [opencode run]
  collect   （无 GitHub 令牌）收集补丁
  publish   （App 令牌）开清理 PR / 告警 issue / 经验 PR，更新游标

python -m wf.loops.cleanup prepare
python -m wf.loops.cleanup analyze --repo maptoposter --workdir target --since <sha> --out out
python -m wf.loops.cleanup collect --workdir target --out out
python -m wf.loops.cleanup publish --results cleanup-results
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from .. import markers, patches, prompts, schemas
from ..config import load_config
from ..lessons import merge_lessons
from ..llm import LLM
from .common import Ctx, git, git_ok, read_json, set_output, setup, step_summary, write_json

LESSONS_PATH = "lessons/LESSONS.md"


def prepare(ctx: Ctx):
    state, _ = ctx.store.load()
    matrix = [{"key": r.name, "repo": r.full_name, "branch": r.default_branch,
               "since": state["cursors"].get(f"cleanup:{r.name}", "")} for r in ctx.cfg.repos.values()]
    set_output("matrix", matrix)


def diff_range(workdir: Path, since: str, days: int) -> tuple[str, str] | None:
    head = git("rev-parse", "HEAD", cwd=workdir).strip()
    if since and git_ok("merge-base", "--is-ancestor", since, head, cwd=workdir):
        base = since
    else:
        base = git("rev-list", "-1", f"--before={days} days ago", head, cwd=workdir).strip()
    if not base or base == head:
        return None
    return base, head


def analyze(repo_name: str, workdir: Path, since: str, out: Path):
    cfg = load_config()
    repo = cfg.repo(repo_name)
    cp = cfg.policy["cleanup"]
    out.mkdir(parents=True, exist_ok=True)
    head = git("rev-parse", "HEAD", cwd=workdir).strip()
    rng = diff_range(workdir, since, cp["lookback_days"])
    result = {"findings": [], "lessons": []}
    if rng:
        base, head = rng
        log_text = git("log", "--no-merges", "--format=%h %an %s", f"{base}..{head}", cwd=workdir)
        diff = git("diff", "--stat", "--patch", f"{base}..{head}", "--", ".", ":!*.lock", ":!*lock.json",
                   ":!*.pbxproj", cwd=workdir)
        user = "\n\n".join([
            f"仓库：{repo.full_name}（{repo.default_branch}），区间 {base[:7]}..{head[:7]}",
            prompts.untrusted("commits", log_text, 8000),
            prompts.untrusted("diff", diff, cp["max_diff_chars"]),
        ])
        result = LLM.from_config(cfg.bot).chat_json(prompts.system("cleanup.md"), user, schemas.CLEANUP)
    write_json(out / "findings.json", {"repo": repo.name, "head": head, **result})
    fixable = [f for f in result["findings"] if f["fixable"] and f["kind"] not in ("suspicious", "malicious")]
    if fixable:
        text = "\n".join(f"- `{f['file']}` [{f['kind']}/{f['severity']}] {f['description']}；建议：{f.get('fix_hint', '')}"
                         for f in fixable)
        (out / "fix-prompt.md").write_text(prompts.render("cleanup-fix.md", findings=text), encoding="utf-8")
    set_output("needs_fix", "true" if fixable else "false")
    print(f"{repo.name}: {len(result['findings'])} findings, {len(fixable)} fixable")


def collect(workdir: Path, out: Path):
    cfg = load_config()
    patch, files, summary = patches.collect(workdir)
    errs = patches.problems(patch, files, summary, cfg.policy["fix"]["forbidden_prefixes"],
                            [os.environ.get("OPENCODE_API_KEY", "")])
    patches.write_patch(out / "change.patch", "" if errs else patch)
    (out / "summary.md").write_text(summary, encoding="utf-8")
    write_json(out / "patch.json", {"ok": not errs, "problems": errs, "files": files})


def publish(ctx: Ctx, results: Path):
    cfg, gh, bot = ctx.cfg, ctx.gh, ctx.rt.bot_login
    footer = "\n\n---\n" + cfg.replies.get("footer", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    today = date.today().isoformat()
    rows = []
    for d in sorted(p for p in results.iterdir() if p.is_dir()) if results.exists() else []:
        f = read_json(d / "findings.json")
        if not f:
            continue
        repo = cfg.repo(f["repo"])
        ctx.rec.cursors[f"cleanup:{repo.name}"] = f["head"]
        for les in f.get("lessons", []):
            ctx.rec.lesson(les["category"], les["text"], f"cleanup:{repo.name}@{f['head'][:7]}")
        bad = [x for x in f["findings"] if x["kind"] in ("suspicious", "malicious")]
        if bad:
            body = "\n".join(f"- **{x['kind']} / {x['severity']}** `{x['file']}`：{markers.sanitize(x['description'], 600)}"
                             for x in bad)
            gh.create_issue(cfg.control_repo, f"[可疑代码] {repo.name} 体检发现 {len(bad)} 处",
                            f"`{repo.full_name}` @ `{f['head'][:7]}` 体检结果，需要维护者人工核查：\n\n{body}{footer}",
                            ["bot:suspicious", "bot:needs-human"])
        meta = read_json(d / "patch.json")
        patch = patches.read_patch(d / "change.patch")
        pr_url = ""
        has_open = any(p["head"]["ref"].startswith("bot/cleanup-") for p in gh.list_open_pulls(repo.full_name))
        if has_open:
            pr_url = "上一个清理 PR 未处理，本次不再开新的"
        elif meta and meta["ok"] and patch.strip():
            branch = f"bot/cleanup-{today}-{run_id}"
            title = f"refactor: 定期清理 {today}"
            summary = markers.sanitize((d / "summary.md").read_text(encoding="utf-8"), 4000)
            try:
                if not gh.dry_run:
                    patches.push_branch(os.environ["GH_TOKEN"], repo.full_name, repo.default_branch, branch, patch,
                                        title, bot, f"{os.environ.get('BOT_USER_ID', '0')}+{bot}@users.noreply.github.com")
                pr = gh.create_pull(repo.full_name, branch, repo.default_branch, title,
                                    f"定期体检发现的可安全重构项（行为不变）。\n\n{summary}\n\n"
                                    "此 PR 会经过沙箱验证和审查，维护者 `/approve` 后才会合并。" + footer)
                if pr.get("number"):
                    gh.add_labels(repo.full_name, pr["number"], ["bot:cleanup"])
                pr_url = pr.get("html_url", "")
            except RuntimeError as e:
                pr_url = f"推送失败：{str(e)[:120]}"
        rows.append(f"| {repo.name} | {len(f['findings'])} | {len(bad)} | {pr_url or '—'} |")
        ctx.rec.event(repo.name, "cleanup:done", f["head"][:7], pr_url)
    step_summary([f"## 清理（dry_run={ctx.rt.dry_run}）", "", "| 仓库 | 发现 | 可疑 | 清理 PR |", "|---|---|---|---|", *rows])


def lessons_pr(ctx: Ctx):
    cfg, gh = ctx.cfg, ctx.gh
    state, _ = ctx.store.load()
    pending = state.get("pending_lessons", [])
    if not pending:
        return
    for pr in gh.list_open_pulls(cfg.control_repo):
        if pr["head"]["ref"].startswith("bot/lessons-") and markers.is_bot(pr, ctx.rt.bot_login):
            return   # 上一个经验 PR 还没处理，这次先攒着
    main = cfg.bot["control_default_branch"]
    got = gh.get_file(cfg.control_repo, LESSONS_PATH, main)
    md, sha = got if got else ("# 经验教训\n", None)
    new = merge_lessons(md, pending)
    taken = {json.dumps(x, sort_keys=True, ensure_ascii=False) for x in pending}

    def clear(s: dict):
        s["pending_lessons"] = [x for x in s["pending_lessons"]
                                if json.dumps(x, sort_keys=True, ensure_ascii=False) not in taken]

    if new != md:
        branch = f"bot/lessons-{date.today().isoformat()}-{os.environ.get('GITHUB_RUN_ID', 'local')}"
        gh.create_branch(cfg.control_repo, branch, gh.get_ref(cfg.control_repo, main))
        res = gh.put_file(cfg.control_repo, LESSONS_PATH, new, f"lessons: 新增 {len(pending)} 条经验", branch, sha)
        head = (res or {}).get("commit", {}).get("sha", "dry-run")
        items = "\n".join(f"- [{x['category']}] {x['text']}（{x['source']}）" for x in pending)
        pr = gh.create_pull(cfg.control_repo, branch, main, f"lessons: 新增 {len(pending)} 条经验",
                            f"审查 / 审批 / 清理过程中累积的经验：\n\n{items}")
        if pr.get("number"):
            gh.comment(cfg.control_repo, pr["number"],
                       "维护者评论 `/approve` 合并，`/reject` 丢弃。\n" + markers.make("await", action="merge", sha=head))
            gh.add_labels(cfg.control_repo, pr["number"], ["bot:awaiting-approval"])
    if not gh.dry_run:
        ctx.store.update(clear, "state: lessons archived")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare")
    a_ = sub.add_parser("analyze")
    a_.add_argument("--repo", required=True)
    a_.add_argument("--workdir", required=True)
    a_.add_argument("--since", default="")
    a_.add_argument("--out", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--workdir", required=True)
    c.add_argument("--out", required=True)
    sub.add_parser("publish").add_argument("--results", default="cleanup-results")
    a = ap.parse_args()
    if a.cmd == "analyze":
        analyze(a.repo, Path(a.workdir), a.since, Path(a.out))
        return
    if a.cmd == "collect":
        collect(Path(a.workdir), Path(a.out))
        return
    ctx = setup(f"cleanup-{a.cmd}")
    if a.cmd == "prepare":
        prepare(ctx)
        return
    publish(ctx, Path(a.results))
    ctx.rec.flush(ctx.store)
    lessons_pr(ctx)


if __name__ == "__main__":
    main()
