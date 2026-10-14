"""loop 失败告警：同一个 workflow 只保留一个打开的告警 issue，重复失败追加评论。

python -m wf.loops.alert --workflow "PR loop"
"""
from __future__ import annotations

import argparse

from .. import markers
from .common import setup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workflow", required=True)
    a = ap.parse_args()
    ctx = setup("alert")
    cfg, gh = ctx.cfg, ctx.gh
    slug = "".join(ch if ch.isalnum() else "-" for ch in a.workflow.lower())
    text = f"`{a.workflow}` 运行失败：{ctx.rt.run_url or '(无链接)'}"
    for it in gh.list_open_items(cfg.control_repo, "bot:alert"):
        kv = markers.find(it.get("body", ""), "alert") if markers.is_bot(it, ctx.rt.bot_login) else None
        if kv and kv.get("workflow") == slug:
            gh.comment(cfg.control_repo, it["number"], "再次失败：" + text)
            return
    gh.create_issue(cfg.control_repo, f"[告警] {a.workflow} 失败",
                    text + "\n\n修复后手动关闭此 issue。\n" + markers.make("alert", workflow=slug), ["bot:alert"])


if __name__ == "__main__":
    main()
