"""审批 loop：轮询带 bot:awaiting-approval 的条目，执行维护者本人的 /approve 或 /reject。

动作（来自机器人自己写的 await 标记，别人伪造的忽略）：
  merge      合并 PR（先核对 head SHA 未变）→ 合并后处理发版 / 回复
  release    发布 Release 草稿，回复所有 bot:awaiting-release 条目
  blacklist  把账号写进 config/blacklist.yml

python -m wf.loops.approval
"""
from __future__ import annotations

from datetime import date

import yaml

from .. import markers, release
from ..config import Repo
from ..github import GitHubError
from .common import Ctx, log, setup, step_summary

AWAIT = "bot:awaiting-approval"


def _done(text: str, replies: dict) -> str:
    return f"{text}\n\n---\n{replies.get('footer', '')}\n{markers.make('done')}"


def pending_action(item: dict, comments: list[dict], bot: str) -> tuple[dict, str] | None:
    """返回 (await 标记, 标记时间)。以机器人最新的 await 评论为准，其次是机器人自己开的 issue 正文。"""
    mark = markers.latest_bot_marker(comments, bot, "await")
    if mark:
        return mark[0], mark[1]["created_at"]
    if markers.is_bot(item, bot):
        kv = markers.find(item.get("body", ""), "await")
        if kv:
            return kv, item["created_at"]
    return None


# ------------------------------------------------------------------ merge
def handle_merge(ctx: Ctx, repo_full: str, repo: Repo | None, item: dict, kv: dict, cmd: str, reason: str):
    gh, replies = ctx.gh, ctx.cfg.replies
    n = item["number"]
    pr = gh.get_pull(repo_full, n)
    ref = f"{repo_full.split('/')[-1]}#{n}"
    if pr["state"] != "open":
        gh.remove_label(repo_full, n, AWAIT)
        return "already-closed"
    if cmd == "reject":
        gh.comment(repo_full, n, _done(f"维护者决定不合并。{('理由：' + reason) if reason else ''}", replies))
        gh.remove_label(repo_full, n, AWAIT)
        gh.close_pull(repo_full, n)
        ctx.rec.lesson("误判", f"审查判定可合并但维护者拒绝（{ref}）：{reason or '未写理由'}", ref)
        fix_issue = _fix_issue(pr, ctx.rt.bot_login)
        if fix_issue and repo:
            gh.add_labels(repo_full, fix_issue, ["bot:fix-failed", "bot:needs-human"])
            gh.remove_label(repo_full, fix_issue, "bot:fix-pr-open")
        return "rejected"
    if pr["head"]["sha"] != kv.get("sha"):
        gh.comment(repo_full, n, _done("审查之后 PR 又有新提交，本次批准作废，等待重新审查。", replies))
        gh.remove_label(repo_full, n, AWAIT)
        return "sha-changed"
    method = repo.merge_method if repo else "squash"
    try:
        res = gh.merge_pull(repo_full, n, kv["sha"], method)
    except GitHubError as e:
        gh.comment(repo_full, n, _done(f"合并失败：`{str(e)[:300]}`，需要人工处理。", replies))
        gh.remove_label(repo_full, n, AWAIT)
        gh.add_labels(repo_full, n, ["bot:needs-human"])
        return "merge-failed"
    gh.remove_label(repo_full, n, AWAIT)
    if repo:
        post_merge(ctx, repo, pr, res.get("sha") or kv["sha"])
    else:
        gh.comment(repo_full, n, _done("已按维护者批准合并。", replies))
    return "merged"


def _fix_issue(pr: dict, bot: str) -> int | None:
    if not markers.is_bot(pr, bot):
        return None
    kv = markers.find(pr.get("body", ""), "fix")
    return int(kv["issue"]) if kv and kv.get("issue", "").isdigit() else None


def post_merge(ctx: Ctx, repo: Repo, pr: dict, merge_sha: str):
    gh, replies, n = ctx.gh, ctx.cfg.replies, pr["number"]
    fix_issue = _fix_issue(pr, ctx.rt.bot_login)
    targets = [n] + ([fix_issue] if fix_issue else [])
    if repo.release_mode == "tag":
        draft = release.add_to_draft(gh, repo, merge_sha, f"{pr['title']} (#{n}) by {pr['user']['login']}")
        release.ensure_tracking_issue(gh, ctx.cfg.control_repo, repo, draft, ctx.rt.bot_login)
        msg = f"已合并，将在下一个版本（草稿 `{draft.get('tag_name')}`）发布，发布后会在这里通知。"
        for t in targets:
            gh.comment(repo.full_name, t, _done(msg if t == n else f"修复已在 #{n} 合并。" + msg, replies))
            gh.add_labels(repo.full_name, t, ["bot:awaiting-release"])
    else:
        gh.comment(repo.full_name, n, _done("已合并到主分支，会随下一个正式版本发布。", replies))
        if fix_issue:
            gh.comment(repo.full_name, fix_issue, _done(f"修复已在 #{n} 合并，会随下一个正式版本发布。", replies))
            gh.close_issue(repo.full_name, fix_issue, "completed")
    if fix_issue:
        gh.remove_label(repo.full_name, fix_issue, "bot:fix-pr-open")
    ctx.rec.event(repo.name, "pr:merged", f"{repo.name}#{n}", merge_sha[:7])


# ------------------------------------------------------------------ release
def handle_release(ctx: Ctx, item: dict, kv: dict, cmd: str, reason: str):
    gh, cfg, replies = ctx.gh, ctx.cfg, ctx.cfg.replies
    n = item["number"]
    repo = cfg.repo(kv.get("repo", ""))
    if cmd == "reject":
        gh.comment(cfg.control_repo, n, _done("暂不发布，草稿保留。下次有合并时会重新提醒。", replies))
        gh.close_issue(cfg.control_repo, n, "not_planned")
        return "release-deferred"
    draft = release.find_draft(gh, repo.full_name)
    if draft is None:
        gh.comment(cfg.control_repo, n, _done("没有找到 Release 草稿（可能已手动发布或删除）。", replies))
        gh.close_issue(cfg.control_repo, n, "not_planned")
        return "no-draft"
    pub = gh.update_release(repo.full_name, draft["id"], draft=False, tag_name=draft["tag_name"],
                            target_commitish=draft.get("target_commitish"))
    tag, url = draft["tag_name"], pub.get("html_url", draft.get("html_url", ""))
    items = list(gh.paginate(f"/repos/{repo.full_name}/issues", {"state": "all", "labels": "bot:awaiting-release"}))
    for it in items:
        gh.comment(repo.full_name, it["number"], _done(f"已在 [{tag}]({url}) 发布 🎉", replies))
        gh.remove_label(repo.full_name, it["number"], "bot:awaiting-release")
        if it["state"] == "open" and "pull_request" not in it:
            gh.close_issue(repo.full_name, it["number"], "completed")
    gh.comment(cfg.control_repo, n, _done(f"已发布 {tag}：{url}，通知了 {len(items)} 个 issue / PR。", replies))
    gh.close_issue(cfg.control_repo, n, "completed")
    ctx.rec.event(repo.name, "release:published", tag, url)
    return "released"


# ------------------------------------------------------------------ blacklist
def add_to_blacklist(text: str, login: str, reason: str) -> str:
    data = yaml.safe_load(text) or {}
    users = data.get("users") or []
    if any((u["login"] if isinstance(u, dict) else str(u)).lower() == login.lower() for u in users):
        return text
    users.append({"login": login, "reason": reason, "added": date.today().isoformat()})
    data["users"] = users
    header = "# 只由审批 loop 在维护者 /approve 后写入；也可以手工编辑。\n"
    return header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def handle_blacklist(ctx: Ctx, item: dict, kv: dict, cmd: str, reason: str):
    gh, cfg, replies = ctx.gh, ctx.cfg, ctx.cfg.replies
    n, login = item["number"], kv.get("user", "")
    if cmd == "reject" or not login:
        gh.comment(cfg.control_repo, n, _done("维护者决定不拉黑。", replies))
        gh.close_issue(cfg.control_repo, n, "not_planned")
        return "blacklist-rejected"
    branch = cfg.bot["control_default_branch"]
    got = gh.get_file(cfg.control_repo, "config/blacklist.yml", branch)
    text, sha = got if got else ("users: []\n", None)
    new = add_to_blacklist(text, login, reason or f"累计低质量 PR，见 #{n}")
    if new != text:
        gh.put_file(cfg.control_repo, "config/blacklist.yml", new, f"blacklist: add {login}", branch, sha)
    gh.comment(cfg.control_repo, n, _done(f"已将 `{login}` 加入黑名单。", replies))
    gh.close_issue(cfg.control_repo, n, "completed")
    ctx.rec.event("workflow", "blacklist:added", login)
    return "blacklisted"


# ------------------------------------------------------------------ main
def run(ctx: Ctx) -> list[str]:
    cfg, gh, bot = ctx.cfg, ctx.gh, ctx.rt.bot_login
    rows = []
    targets = [(r.full_name, r) for r in cfg.repos.values()] + [(cfg.control_repo, None)]
    for repo_full, repo in targets:
        for item in gh.list_open_items(repo_full, AWAIT):
            comments = gh.list_comments(repo_full, item["number"])
            pend = pending_action(item, comments, bot)
            if pend is None:
                continue
            kv, after = pend
            dec = markers.maintainer_decision(comments, after, cfg.maintainer)
            if dec is None:
                continue
            cmd, _sub, reason, _c = dec
            action = kv.get("action")
            ref = f"{repo_full}#{item['number']}"
            try:
                if action == "merge" and "pull_request" in item:
                    out = handle_merge(ctx, repo_full, repo, item, kv, cmd, reason)
                elif action == "release" and repo is None:
                    out = handle_release(ctx, item, kv, cmd, reason)
                elif action == "blacklist" and repo is None:
                    out = handle_blacklist(ctx, item, kv, cmd, reason)
                else:
                    log.warning("%s 未知或不匹配的 await 动作 %s", ref, action)
                    continue
            except GitHubError as e:
                log.error("%s 执行失败: %s", ref, e)
                out = f"error:{e.status}"
            ctx.rec.event(repo_full.split("/")[-1], f"approval:{out}", ref, cmd)
            rows.append(f"| {ref} | {action} | {cmd} | {out} |")
    return rows


def main():
    ctx = setup("approval")
    rows = run(ctx)
    ctx.rec.flush(ctx.store)
    step_summary([f"## 审批（dry_run={ctx.rt.dry_run}）", "", "| 条目 | 动作 | 指令 | 结果 |", "|---|---|---|---|", *rows]
                 if rows else ["## 审批：没有新的维护者指令"])


if __name__ == "__main__":
    main()
