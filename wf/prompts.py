"""加载 prompts/*.md 并渲染。外部内容统一包进 <untrusted>。"""
from __future__ import annotations

from .config import ROOT

PROMPTS = ROOT / "prompts"
LESSONS = ROOT / "lessons" / "LESSONS.md"
MAX_LESSON_CHARS = 8000


def lessons_text() -> str:
    try:
        text = LESSONS.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "（暂无）"
    return text[-MAX_LESSON_CHARS:]


def render(name: str, **values: str) -> str:
    text = (PROMPTS / name).read_text(encoding="utf-8")
    values.setdefault("lessons", lessons_text())
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def system(task: str) -> str:
    return render("_shared.md") + "\n\n" + render(task)


def untrusted(label: str, content: str, limit: int = 20000) -> str:
    content = (content or "").replace("</untrusted>", "</ untrusted>")
    if len(content) > limit:
        content = content[:limit] + "\n…（已截断）"
    return f'<untrusted source="{label}">\n{content}\n</untrusted>'
