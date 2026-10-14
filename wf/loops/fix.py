"""issue 修复 loop：

  discover  （App 令牌）挑出 bot:accepted 的 issue，打 bot:fix-in-progress，导出 issue 内容
  prompt    （无 GitHub 令牌）生成给 opencode 的提示词
  [opencode run]  在 workflow YAML 里执行，bash / 网络工具已禁用
  collect   （无 GitHub 令牌）收集补丁并做安全校验
  publish   （App 令牌，不跑外部代码）推到 bot/issue-N-<run> 分支并开 PR；PR 随后走正常的审查 + 审批

python -m wf.loops.fix discover --out out/fix
python -m wf.loops.fix prompt --item out/fix/<key>.json > prompt.md
python -m wf.loops.fix collect --item ... --workdir target --out result
python -m wf.loops.fix publish --results fix-results
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .. import markers, patches, prompts
from ..config import load_config
from .common import Ctx, label_names, log, read_json, set_output, setup, step_summary, write_json

BUSY = {"bot:fix-in-progress", "bot:fix-pr-open", "bot:fix-failed", "bot:needs-human"}


def discover(ctx: Ctx, out: Path):
    cfg, gh, bot = ctx.cfg, ctx.gh, ctx.rt.bot_login
    limit = cfg.policy["fix"]["max_per_run"]
    matrix = []
    for repo in cfg.repos.values():
        for issue in gh.list_open_issues(repo.full_name, labels="bot:accepted"):
            if len(matrix) >= limit:
                break
            if label_names(issue) & BUSY:
                continue
            comments = [c for c in gh.list_comments(repo.full_name, issue["number"]) if not markers.is_bot(c, bot)]
            key = f"{repo.name}-{issue['number']}"
            thread = "\n\n".join(f"[{c['user']['login']}]\n{c.get('body') or ''}" for c in comments[-20:])
            write_json(out / f"{key}.json", {
                "key": key, "repo": repo.name, "number": issue["number"], "title": issue["title"],
                "text": f"标题：{issue['title']}\n\n{issue.get('body') or ''}\n\n--- 评论 ---\n{thread}",
            })
            gh.add_labels(repo.full_name, issue["number"], ["bot:fix-in-progress"])
            matrix.append({"key": key, "repo": repo.full_name, "number": issue["number"], "branch": repo.default_branch,
                           "submodules": repo.submodules, "lfs": repo.lfs})
            ctx.rec.event(repo.name, "fix:started", key)
    set_output("matrix", matrix)
    set_output("has_work", "true" if matrix else "false")
    step_summary([f"## 修复 discover：{len(matrix)} 个 issue"] + [f"- {m['key']}" for m in matrix])


def make_prompt(item_file: Path) -> str:
    item = read_json(item_file)
    return prompts.render("issue-fix.md", issue=item["text"].replace("</untrusted>", "</ untrusted>")[:30000])


def collect(item_file: Path, workdir: Path, out: Path):
    cfg = load_config()
    item = read_json(item_file)
    patch, files, summary = patches.collect(workdir)
    errs = patches.problems(patch, files, summary, cfg.policy["fix"]["forbidden_prefixes"],
                            [os.environ.get("OPENCODE_API_KEY", "")])
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "result.json", {**{k: item[k] for k in ("key", "repo", "number", "title")},
                                     "ok": not errs, "problems": errs, "files": files})
    (out / "summary.md").write_text(summary, encoding="utf-8")
    patches.write_patch(out / "change.patch", "" if errs else patch)
    print(f"{item['key']}: ok={not errs} files={len(files)} problems={errs}")


def publish(ctx: Ctx, results_dir: Path):
    cfg, gh, bot, replies = ctx.cfg, ctx.gh, ctx.rt.bot_login, ctx.cfg.replies
    footer = "\n\n---\n" + replies.get("footer", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    bot_id = os.environ.get("BOT_USER_ID", "0")
    rows = []
    dirs = sorted(p for p in results_dir.iterdir() if p.is_dir()) if results_dir.exists() else []
    results = [r for r in (read_json(d / "result.json") for d in dirs) if r]
    # 被选中但没产出结果的（opencode 崩溃 / 超时），也要把 in-progress 摘掉
    seen = {r["key"] for r in results}
    for m in json.loads(os.environ.get("FIX_MATRIX", "[]")):
        if m["key"] not in seen:
            gh.remove_label(m["repo"], m["number"], "bot:fix-in-progress")
            gh.add_labels(m["repo"], m["number"], ["bot:fix-failed", "bot:needs-human"])
            rows.append(f"| {m['key']} | 无结果 | 修复 job 没有产出 |")
    for d in dirs:
        res = read_json(d / "result.json")
        if not res:
            continue
        repo = cfg.repo(res["repo"])
        n = res["number"]
        summary = markers.sanitize((d / "summary.md").read_text(encoding="utf-8"), 4000)
        patch = patches.read_patch(d / "change.patch")
        gh.remove_label(repo.full_name, n, "bot:fix-in-progress")
        if not res["ok"] or not patch.strip():
            reason = "；".join(res.get("problems") or ["没有产生补丁"])
            gh.comment(repo.full_name, n, f"自动修复没有完成：{reason}\n\n维护者会人工跟进。{footer}")
            gh.add_labels(repo.full_name, n, ["bot:fix-failed", "bot:needs-human"])
            rows.append(f"| {res['key']} | 失败 | {reason[:80]} |")
            continue
        branch = f"bot/issue-{n}-{run_id}"
        title = f"fix: {res['title'][:60]} (#{n})"
        try:
            if gh.dry_run:
                log.info("[DRY_RUN] push %s %s (%d files)", repo.full_name, branch, len(res["files"]))
            else:
                patches.push_branch(os.environ["GH_TOKEN"], repo.full_name, repo.default_branch, branch, patch,
                                    f"{title}\n\nRefs #{n}", bot, f"{bot_id}+{bot}@users.noreply.github.com")
        except RuntimeError as e:
            gh.comment(repo.full_name, n, f"自动修复补丁推送失败：`{str(e)[:300]}`，维护者会人工跟进。{footer}")
            gh.add_labels(repo.full_name, n, ["bot:fix-failed", "bot:needs-human"])
            rows.append(f"| {res['key']} | 推送失败 | {str(e)[:80]} |")
            continue
        body = (f"自动修复 #{n}（Refs #{n}）。\n\n{summary or '（无说明）'}\n\n改动文件：\n"
                + "\n".join(f"- `{f}`" for f in res["files"][:50])
                + "\n\n此 PR 会经过与社区 PR 相同的沙箱验证和审查，维护者 `/approve` 后才会合并。"
                + footer + "\n" + markers.make("fix", issue=n))
        pr = gh.create_pull(repo.full_name, branch, repo.default_branch, title, body)
        if pr.get("number"):
            gh.add_labels(repo.full_name, pr["number"], ["bot:auto-fix"])
        gh.comment(repo.full_name, n, f"已提交修复 PR {pr.get('html_url', '')}，审查和维护者确认后合并。{footer}")
        gh.add_labels(repo.full_name, n, ["bot:fix-pr-open"])
        ctx.rec.event(repo.name, "fix:pr-opened", res["key"], pr.get("html_url", ""))
        rows.append(f"| {res['key']} | PR 已开 | {pr.get('html_url', '')} |")
    step_summary([f"## 修复 publish（dry_run={ctx.rt.dry_run}）", "", "| issue | 结果 | 说明 |", "|---|---|---|", *rows])


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover").add_argument("--out", default="out/fix")
    sub.add_parser("prompt").add_argument("--item", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--item", required=True)
    c.add_argument("--workdir", required=True)
    c.add_argument("--out", required=True)
    sub.add_parser("publish").add_argument("--results", default="fix-results")
    a = ap.parse_args()
    if a.cmd == "prompt":
        sys.stdout.write(make_prompt(Path(a.item)))
        return
    if a.cmd == "collect":
        collect(Path(a.item), Path(a.workdir), Path(a.out))
        return
    ctx = setup(f"fix-{a.cmd}")
    if a.cmd == "discover":
        discover(ctx, Path(a.out))
    else:
        publish(ctx, Path(a.results))
    ctx.rec.flush(ctx.store)


if __name__ == "__main__":
    main()
