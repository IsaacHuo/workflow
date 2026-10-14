"""OpenAI 兼容的 chat completions 客户端（opencode Inference API）。

只做"判断"：强制输出 JSON，按 JSON Schema 校验，不合法时带着错误重试一次。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid

import jsonschema
import requests

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMOutputError(LLMError):
    """模型两次都没给出合法 JSON。调用方应把该条目转人工。"""


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict:
    text = text.strip()
    candidates = [text]
    candidates += _FENCE.findall(text)
    if "{" in text and "}" in text:
        candidates.append(text[text.index("{"): text.rindex("}") + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("输出里找不到 JSON 对象")


class LLM:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: int = 180,
                 session: requests.Session | None = None, sleep=time.sleep, max_attempts: int = 4,
                 user_agent: str = "isaachuo-workflow-bot/0.1"):
        if not api_key:
            raise LLMError("缺少 OPENCODE_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                               "User-Agent": user_agent})
        self._sleep = sleep
        self.max_attempts = max_attempts
        self.json_mode = True

    @classmethod
    def from_config(cls, bot_cfg: dict) -> "LLM":
        llm = bot_cfg["llm"]
        return cls(
            api_key=os.environ.get("OPENCODE_API_KEY", ""),
            base_url=os.environ.get("LLM_BASE_URL", llm["base_url"]),
            model=os.environ.get("LLM_MODEL", llm["model"]),
            timeout=int(llm.get("timeout_seconds", 180)),
            user_agent=llm.get("user_agent", "isaachuo-workflow-bot/0.1"),
        )

    def chat(self, messages: list[dict], temperature: float = 0.2, session_id: str | None = None) -> str:
        body = {"model": self.model, "messages": messages, "temperature": temperature}
        headers = {"x-opencode-session": session_id or uuid.uuid4().hex}
        last = ""
        for attempt in range(self.max_attempts):
            if self.json_mode:
                body["response_format"] = {"type": "json_object"}
            else:
                body.pop("response_format", None)
            try:
                r = self.s.post(f"{self.base_url}/chat/completions", json=body, headers=headers, timeout=self.timeout)
            except requests.RequestException as e:
                last = str(e)
                self._sleep(5 * 2 ** attempt)
                continue
            if r.status_code == 400 and self.json_mode and "response_format" in r.text:
                log.warning("网关不支持 response_format，改为纯提示词约束 JSON")
                self.json_mode = False
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = f"{r.status_code} {r.text[:200]}"
                self._sleep(5 * 2 ** attempt)
                continue
            if r.status_code >= 400:
                raise LLMError(f"LLM {r.status_code}: {r.text[:500]}")
            data = r.json()
            try:
                return data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as e:
                raise LLMError(f"响应格式异常: {str(data)[:300]}") from e
        raise LLMError(f"LLM 多次重试失败: {last}")

    def chat_json(self, system: str, user: str, schema: dict) -> dict:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        session_id = uuid.uuid4().hex          # 同一段对话（含纠错重试）用同一个 session
        err = ""
        for _ in range(2):
            text = self.chat(messages, session_id=session_id)
            try:
                obj = extract_json(text)
                jsonschema.validate(obj, schema)
                return obj
            except (ValueError, jsonschema.ValidationError) as e:
                err = getattr(e, "message", str(e))
                messages += [
                    {"role": "assistant", "content": text[:4000]},
                    {"role": "user", "content": f"上一次输出不合法：{err}。只输出一个符合要求的 JSON 对象，不要任何其他文字。"},
                ]
        raise LLMOutputError(err)
