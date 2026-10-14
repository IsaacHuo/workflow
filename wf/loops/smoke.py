"""冒烟测试：直接调一次 Inference API，确认密钥、接口路径和模型名可用。

python -m wf.loops.smoke
"""
from __future__ import annotations

import logging

from ..config import load_config
from ..llm import LLM


def main():
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()
    llm = LLM.from_config(cfg.bot)
    schema = {"type": "object", "required": ["ok", "model"], "properties": {"ok": {"const": True}, "model": {"type": "string"}}}
    out = llm.chat_json("只输出 JSON。", '回复 {"ok": true, "model": "<你的模型名>"}', schema)
    print(f"model={llm.model} json_mode={llm.json_mode} reply={out}")


if __name__ == "__main__":
    main()
