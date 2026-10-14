"""每日汇总：过去 24 小时的动作 + 当前所有待你处理的条目，开在 control 仓库。

python -m wf.loops.digest
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from .. import markers
from ..policy import LABELS
from .common import Ctx, setup, step_summary

WATCH = ["bot:awaiting-approval", "bot:needs-human", "bot:suspicious"]


def render(events: list[dict], pending: list[tuple[str, str, dict]], since: str) -> str:
    recent = [e for e in events if e["ts"] >= since]
    counts = Counter((e["repo"], e["kind"]) for e in recent)
    lines = ["## 过去 24 小时", ""]
    if counts:
        lines += ["| 仓库 | 动作 | 次数 |", "|---|---|---|"]
        lines += [f"| {r} | {k} | {n} |" for (r, k), n in sorted(counts.items())]
    else:
        lines.append("没有动作。")
    lines += ["", "## 待你处理", ""]
    if pending:
        lines += [f"- [ ] `{label}` [{repo}#{it['number']}]({it['html_url']}) {it['title']}" for repo, label, it in pending]
    else:
        lines.append("无。")
    return "\n".join(lines)


def run(ctx: Ctx):
    cfg, gh, bot = ctx.cfg, ctx.gh, ctx.rt.bot_login
    state, _ = ctx.store.load()
    since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pending, seen = [], set()
    for repo_full in [r.full_name for r in cfg.repos.values()] + [cfg.control_repo]:
        for label in WATCH:
            for it in gh.list_open_items(repo_full, label):
                if (repo_full, it["number"]) not in seen:
                    seen.add((repo_full, it["number"]))
                    pending.append((repo_full.split("/")[-1], label, it))
    body = render(state.get("events", []), pending, since) + "\n\n" + markers.make("digest")
    for it in gh.list_open_items(cfg.control_repo, "bot:digest"):
        if markers.is_bot(it, bot):
            gh.close_issue(cfg.control_repo, it["number"], "completed")
    today = datetime.now(timezone.utc).date().isoformat()
    gh.ensure_labels(cfg.control_repo, LABELS)
    gh.create_issue(cfg.control_repo, f"[日报] {today}", body, ["bot:digest"])
    step_summary([body])


def main():
    run(setup("digest"))


if __name__ == "__main__":
    main()
