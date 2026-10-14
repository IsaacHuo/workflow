"""把待归档的经验条目合并进 lessons/LESSONS.md（按 ## 类别 归档，按正文去重）。"""
from __future__ import annotations


def merge_lessons(md: str, lessons: list[dict]) -> str:
    lines = md.rstrip("\n").splitlines()
    for les in lessons:
        text = " ".join(les["text"].split())
        if not text or text in md:
            continue
        entry = f"- {text}（来源：{les.get('source', '?')}，{les.get('ts', '')[:10]}）"
        header = f"## {les.get('category', '其他')}"
        if header not in lines:
            lines += ["", header, ""]
        i = lines.index(header) + 1
        while i < len(lines) and not lines[i].startswith("## "):
            i += 1
        while i > 0 and lines[i - 1].strip() == "":   # 插在该节最后一个非空行之后
            i -= 1
        lines.insert(i, entry)
        md = "\n".join(lines)
    return "\n".join(lines) + "\n"
