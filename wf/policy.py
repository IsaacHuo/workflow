"""确定性的动作分级：把模型结论映射成具体动作。

模型输出只是"结论"，能做什么完全由这里决定：
- 自动：评论、打 / 去 label、关闭 issue、关闭外部低质量 PR、记违规、记经验
- 需要维护者 /approve：合并（await action=merge）。发版 / 拉黑 / 清理在各自 loop 里同样走 await。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import markers

LABELS = {
    "bot:triaged": "c5def5",
    "bot:needs-info": "fbca04",
    "bot:accepted": "0e8a16",
    "bot:needs-human": "d93f0b",
    "bot:relay-ad": "bfdadc",
    "bot:reviewed": "c5def5",
    "bot:changes-requested": "fbca04",
    "bot:awaiting-approval": "5319e7",
    "bot:awaiting-release": "5319e7",
    "bot:fix-in-progress": "1d76db",
    "bot:fix-pr-open": "1d76db",
    "bot:fix-failed": "d93f0b",
    "bot:auto-fix": "1d76db",
    "bot:cleanup": "1d76db",
    "bot:suspicious": "b60205",
    "bot:digest": "c2e0c6",
    "bot:alert": "b60205",
    "wontfix": "ffffff",
    "bug": "d73a4a",
    "enhancement": "a2eeef",
    "documentation": "0075ca",
    "question": "d876e3",
}


@dataclass
class Plan:
    comment: str | None = None
    add_labels: list[str] = field(default_factory=list)
    remove_labels: list[str] = field(default_factory=list)
    close: bool = False
    close_reason: str = "not_planned"
    violation: str | None = None
    lesson: tuple[str, str] | None = None
    outcome: str = ""


def _footer(replies: dict) -> str:
    return "\n\n---\n" + replies.get("footer", "")


# ---------------------------------------------------------------- issue
def triage_plan(result: dict, *, author: str, is_maintainer: bool, policy: dict, replies: dict) -> Plan:
    ip = policy["issue"]
    max_chars = policy["reply"]["max_chars"]
    cat = result["category"]
    p = Plan(outcome=cat, add_labels=["bot:triaged"])
    if cat != "needs_info":
        p.remove_labels.append("bot:needs-info")

    if result["confidence"] < ip["min_confidence"]:
        p.add_labels.append("bot:needs-human")
        p.outcome = f"low_confidence:{cat}"
        return p

    if cat == "relay_ad":
        text = replies["relay_ad"]
        p.add_labels.append("bot:relay-ad")
        p.close = ip.get("close_relay_ad", False) and not is_maintainer
    else:
        text = markers.sanitize(result.get("reply", ""), max_chars, {author})
        if not text:
            p.add_labels.append("bot:needs-human")
            p.outcome = f"empty_reply:{cat}"
            return p
        allowed = set(ip.get("allowed_labels", []))
        p.add_labels += [lb for lb in result.get("labels", []) if lb in allowed]
        if cat == "wontfix":
            p.add_labels.append("wontfix")
            p.close = ip.get("close_wontfix", False) and not is_maintainer
        elif cat == "needs_info":
            p.add_labels.append("bot:needs-info")
        elif cat == "question":
            p.add_labels.append("question")
        elif cat == "fix":
            p.add_labels.append("bot:accepted")

    p.add_labels = list(dict.fromkeys(p.add_labels))
    p.comment = text + _footer(replies) + "\n" + markers.make("triage", category=cat)
    return p


# ---------------------------------------------------------------- PR
@dataclass
class ReviewContext:
    repo: str
    number: int
    sha: str
    author: str
    is_maintainer: bool
    is_bot_pr: bool
    verification_ok: bool
    verification_md: str
    has_tests: bool
    sensitive_files: list[str] = field(default_factory=list)


_TITLE = {
    "approve": "✅ 审查通过，等待维护者确认合并",
    "request_changes": "🛠 需要修改",
    "needs_human": "👀 需要维护者人工判断",
    "close": "⛔ 不予合并",
}


def final_verdict(result: dict, ctx: ReviewContext) -> tuple[str, list[str]]:
    """在模型结论之上叠加硬规则，返回 (最终结论, 被改写的原因)。"""
    v = result["verdict"]
    why: list[str] = []

    def downgrade(to: str, reason: str):
        nonlocal v
        v = to
        why.append(reason)

    severe = [f for f in result.get("findings", []) if f["severity"] in ("blocker", "major")]
    malicious = bool(result.get("malicious_suspected"))
    if result.get("relay_ad"):
        v = "close"
    if v == "approve" and not ctx.verification_ok:
        downgrade("request_changes", "实际运行验证未通过或结果缺失，不能判定通过")
    if v == "approve" and severe:
        downgrade("request_changes", "存在 blocker / major 问题")
    if v == "close" and not (result.get("low_quality") or result.get("relay_ad") or malicious):
        downgrade("request_changes", "关闭需要明确的低质量 / 恶意 / 广告判定")
    if v == "close" and (ctx.is_maintainer or ctx.is_bot_pr):
        downgrade("needs_human", "维护者或机器人自己的 PR 不自动关闭")
    if malicious and v in ("approve", "request_changes"):
        downgrade("needs_human", "模型怀疑存在恶意代码")
    return v, why


def review_plan(result: dict, ctx: ReviewContext, *, policy: dict, replies: dict) -> Plan:
    v, why = final_verdict(result, ctx)
    p = Plan(outcome=v, add_labels=["bot:reviewed"])
    for lb in ("bot:awaiting-approval", "bot:changes-requested", "bot:needs-human"):
        p.remove_labels.append(lb)

    if result.get("relay_ad"):
        reply = replies["relay_ad"]
        p.add_labels.append("bot:relay-ad")
    else:
        reply = markers.sanitize(result.get("reply", ""), policy["reply"]["max_chars"], {ctx.author})

    if v == "approve":
        p.add_labels.append("bot:awaiting-approval")
    elif v == "request_changes":
        p.add_labels.append("bot:changes-requested")
    elif v == "needs_human":
        p.add_labels.append("bot:needs-human")
    elif v == "close":
        p.close = True
        if result.get("low_quality") or result.get("malicious_suspected"):
            p.violation = result.get("summary", "")[:200] or "低质量 PR"
    if result.get("malicious_suspected"):
        p.add_labels.append("bot:suspicious")
    if result.get("lesson") and v in ("close", "request_changes", "needs_human"):
        p.lesson = ("不该合并" if v == "close" else "验证不严", result["lesson"])
    for lb in list(p.add_labels):
        if lb in p.remove_labels:
            p.remove_labels.remove(lb)

    findings = "\n".join(
        f"- **[{f['severity']}]** `{f.get('file') or '?'}{':' + str(f['line']) if f.get('line') else ''}` "
        f"{markers.sanitize(f['message'], 500)}"
        for f in result.get("findings", [])
    ) or "- 无"
    notes = []
    if why:
        notes.append("规则改写：" + "；".join(why))
    if not ctx.has_tests:
        notes.append("该仓库无测试，验证范围有限")
    if ctx.sensitive_files:
        notes.append("改动了敏感路径：" + ", ".join(f"`{f}`" for f in ctx.sensitive_files[:10]))
    body = [
        f"### 🤖 自动审查：{_TITLE[v]}",
        "",
        reply,
        "",
        "<details><summary>审查明细</summary>",
        "",
        f"**摘要**：{markers.sanitize(result.get('summary', ''), 1000)}",
        "",
        "**问题**：",
        findings,
        "",
        "**实际运行验证**：",
        ctx.verification_md,
        *(["", "**备注**：" + "；".join(notes)] if notes else []),
        "",
        "</details>",
    ]
    marks = [markers.make("review", sha=ctx.sha, verdict=v)]
    if v == "approve":
        marks.append(markers.make("await", action="merge", sha=ctx.sha))
        body += ["", "维护者评论 `/approve` 合并，`/reject 理由` 关闭。"]
    p.comment = "\n".join(body) + _footer(replies) + "\n" + "\n".join(marks)
    return p


def blacklisted_plan(sha: str, replies: dict) -> Plan:
    return Plan(
        outcome="blacklisted",
        add_labels=["bot:reviewed"],
        close=True,
        comment=replies["blacklisted_pr"] + _footer(replies) + "\n" + markers.make("review", sha=sha, verdict="blacklisted"),
    )


def needs_human_plan(sha: str, reason: str, replies: dict) -> Plan:
    return Plan(
        outcome="needs_human",
        add_labels=["bot:reviewed", "bot:needs-human"],
        remove_labels=["bot:awaiting-approval"],
        comment=f"### 🤖 自动审查：{_TITLE['needs_human']}\n\n{reason}" + _footer(replies) + "\n"
        + markers.make("review", sha=sha, verdict="needs_human"),
    )


def apply_plan(gh, repo: str, number: int, plan: Plan, *, current_labels: set[str], is_pr: bool = False):
    if plan.comment:
        gh.comment(repo, number, plan.comment)
    gh.add_labels(repo, number, [lb for lb in plan.add_labels if lb not in current_labels])
    for lb in plan.remove_labels:
        if lb in current_labels:
            gh.remove_label(repo, number, lb)
    if plan.close:
        if is_pr:
            gh.close_pull(repo, number)
        else:
            gh.close_issue(repo, number, plan.close_reason)
