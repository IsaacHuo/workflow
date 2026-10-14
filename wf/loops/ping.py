"""连通性检查：用 App 令牌列出各仓库未关闭的 issue / PR，确认权限配置正确。只读。

python -m wf.loops.ping
"""
from __future__ import annotations

from .common import setup, step_summary


def main():
    ctx = setup("ping")
    gh = ctx.gh
    lines = [f"## ping（bot={ctx.rt.bot_login}）", ""]
    for full in [r.full_name for r in ctx.cfg.repos.values()] + [ctx.cfg.control_repo]:
        issues = gh.list_open_issues(full)
        pulls = gh.list_open_pulls(full)
        lines.append(f"### {full}：{len(issues)} issue / {len(pulls)} PR")
        lines += [f"- issue #{i['number']} {i['title']}" for i in issues[:20]]
        lines += [f"- PR #{p['number']} {p['title']}（{p['user']['login']}）" for p in pulls[:20]]
        lines.append("")
    state, sha = ctx.store.load()
    lines.append(f"state 分支：{'已存在' if sha else '尚未创建（首次写入时自动创建）'}，"
                 f"待归档经验 {len(state['pending_lessons'])} 条，违规记录 {len(state['violations'])} 人")
    step_summary(lines)


if __name__ == "__main__":
    main()
