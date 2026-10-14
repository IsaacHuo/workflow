"""评论里的隐藏标记 <!-- wf-bot:KIND k=v ... -->，用于去重和记录待审批动作。

只信任机器人账号自己发的评论里的标记，别人伪造的标记一律忽略。
"""
from __future__ import annotations

import re

_MARK = re.compile(r"<!--\s*wf-bot:([a-z-]+)((?:\s+[a-z_]+=[^\s>]+)*)\s*-->")
_KV = re.compile(r"([a-z_]+)=([^\s>]+)")
_CMD = re.compile(r"^/(approve|reject)(?:\s+(release))?(?:\s+(.*))?$", re.I)


def make(kind: str, **kv) -> str:
    attrs = "".join(f" {k}={v}" for k, v in kv.items() if v is not None and v != "")
    return f"<!-- wf-bot:{kind}{attrs} -->"


def parse(body: str) -> list[tuple[str, dict]]:
    return [(m.group(1), dict(_KV.findall(m.group(2) or ""))) for m in _MARK.finditer(body or "")]


def find(body: str, kind: str) -> dict | None:
    for k, kv in parse(body):
        if k == kind:
            return kv
    return None


def is_bot(comment: dict, bot_login: str) -> bool:
    user = comment.get("user") or {}
    return bool(bot_login) and user.get("login", "").lower() == bot_login.lower()


def latest_bot_marker(comments: list[dict], bot_login: str, kind: str) -> tuple[dict, dict] | None:
    """返回 (marker 属性, 评论) —— 机器人发的、带该 kind 标记的最新一条。"""
    for c in reversed(comments):
        if is_bot(c, bot_login):
            kv = find(c.get("body", ""), kind)
            if kv is not None:
                return kv, c
    return None


def parse_command(body: str) -> tuple[str, str | None, str] | None:
    """解析评论首个非空行的 /approve [release] 或 /reject [理由]。"""
    for line in (body or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _CMD.match(line)
        if not m:
            return None
        rest = (m.group(3) or "").strip()
        tail = "\n".join((body or "").strip().splitlines()[1:]).strip()
        return m.group(1).lower(), (m.group(2) or "").lower() or None, (rest + ("\n" + tail if tail else "")).strip()
    return None


def maintainer_decision(comments: list[dict], after: str, maintainer: str) -> tuple[str, str | None, str, dict] | None:
    """在时间点 after（ISO8601）之后，找维护者本人最新的一条指令。"""
    found = None
    for c in comments:
        if c.get("created_at", "") <= after:
            continue
        user = c.get("user") or {}
        if user.get("login", "").lower() != maintainer.lower():
            continue
        if c.get("author_association") != "OWNER":
            continue
        cmd = parse_command(c.get("body", ""))
        if cmd:
            found = (*cmd, c)
    return found


_MENTION = re.compile(r"(?<![\w`])@([A-Za-z0-9][A-Za-z0-9-]{0,38})")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def sanitize(text: str, max_chars: int, allow_mentions: set[str] | None = None) -> str:
    """清理模型生成、要公开发出去的文字：去掉伪造标记、斜杠指令、无关 @、截断长度。"""
    allow = {a.lower() for a in (allow_mentions or set())}
    text = _HTML_COMMENT.sub("", text or "")
    text = text.replace("<!--", "&lt;!--")
    lines = [ln for ln in text.splitlines() if not re.match(r"^\s*/(approve|reject)\b", ln, re.I)]
    text = "\n".join(lines)
    text = _MENTION.sub(lambda m: m.group(0) if m.group(1).lower() in allow else f"@\u200b{m.group(1)}", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text
