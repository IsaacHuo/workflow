"""issue 分诊 loop：拉取未处理的 issue → 模型判断 → policy 映射 → 执行。

python -m wf.loops.issue_triage
"""
from __future__ import annotations

from .. import markers, prompts, schemas
from ..llm import LLM, LLMError, LLMOutputError
from ..policy import LABELS, Plan, apply_plan, triage_plan
from .common import Ctx, is_maintainer, label_names, log, setup, step_summary


def needs_triage(issue: dict, comments: list[dict], bot_login: str) -> bool:
    labels = label_names(issue)
    if "bot:ignore" in labels or markers.is_bot(issue, bot_login):
        return False
    if "bot:triaged" not in labels:
        return True
    if "bot:needs-info" in labels:
        mark = markers.latest_bot_marker(comments, bot_login, "triage")
        if mark is None:
            return False
        author = issue["user"]["login"]
        return any(c["user"]["login"] == author and c["created_at"] > mark[1]["created_at"] for c in comments)
    return False


def build_user_prompt(repo_full: str, description: str, issue: dict, comments: list[dict], bot_login: str) -> str:
    thread = "\n\n".join(
        f"[{'机器人' if markers.is_bot(c, bot_login) else c['user']['login']} @ {c['created_at']}]\n{c.get('body') or ''}"
        for c in comments[-30:]
    )
    meta = f"仓库：{repo_full}\n仓库简介：{description or '（无）'}\n现有 label：{', '.join(sorted(label_names(issue))) or '无'}"
    content = f"标题：{issue['title']}\n作者：{issue['user']['login']}\n\n{issue.get('body') or ''}\n\n--- 评论 ---\n{thread}"
    return meta + "\n\n" + prompts.untrusted("issue", content, 30000)


def run(ctx: Ctx, llm: LLM) -> list[str]:
    cfg, gh, bot = ctx.cfg, ctx.gh, ctx.rt.bot_login
    limit = cfg.policy["issue"]["max_per_run"]
    system = prompts.system("issue-triage.md")
    report, done = [], 0
    for repo in cfg.repos.values():
        gh.ensure_labels(repo.full_name, LABELS)
        description = (gh.get(f"/repos/{repo.full_name}") or {}).get("description", "")
        for issue in gh.list_open_issues(repo.full_name):
            if done >= limit:
                break
            author = issue["user"]["login"]
            if author.lower() in cfg.blacklist:
                continue
            comments = gh.list_comments(repo.full_name, issue["number"])
            if not needs_triage(issue, comments, bot):
                continue
            done += 1
            ref = f"{repo.name}#{issue['number']}"
            try:
                result = llm.chat_json(system, build_user_prompt(repo.full_name, description, issue, comments, bot),
                                       schemas.ISSUE_TRIAGE)
                plan = triage_plan(result, author=author, is_maintainer=is_maintainer(cfg, author),
                                   policy=cfg.policy, replies=cfg.replies)
                reason = result["reason"]
            except LLMOutputError as e:
                plan, reason = Plan(outcome="llm_invalid", add_labels=["bot:triaged", "bot:needs-human"]), str(e)
            except LLMError as e:
                log.error("%s 模型调用失败，本轮跳过: %s", ref, e)
                continue
            apply_plan(gh, repo.full_name, issue["number"], plan, current_labels=label_names(issue))
            ctx.rec.event(repo.name, f"issue:{plan.outcome}", ref, reason)
            report.append(f"| {ref} | {plan.outcome} | {'关闭' if plan.close else ''} | {reason[:80]} |")
    return report


def main():
    ctx = setup("issue-triage")
    report = run(ctx, LLM.from_config(ctx.cfg.bot))
    ctx.rec.flush(ctx.store)
    step_summary([f"## issue 分诊（dry_run={ctx.rt.dry_run}）", "", "| issue | 结论 | 动作 | 理由 |",
                  "|---|---|---|---|", *report] if report else ["## issue 分诊：没有需要处理的 issue"])


if __name__ == "__main__":
    main()
