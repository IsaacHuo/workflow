import json

import pytest

from wf import schemas
from wf.llm import LLM, LLMError, LLMOutputError, extract_json


class Resp:
    def __init__(self, status, data=None, text=""):
        self.status_code = status
        self._data = data
        self.text = text or json.dumps(data or {})

    def json(self):
        return self._data


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.bodies = []
        self.sent_headers = []
        self.headers = {}

    def post(self, url, json=None, headers=None, timeout=None):
        self.bodies.append(json)
        self.sent_headers.append(headers or {})
        return self.responses.pop(0)


def ok(content):
    return Resp(200, {"choices": [{"message": {"content": content}}]})


def make(*responses):
    s = Session(*responses)
    return LLM("k", "https://x/v1", "deepseek-v4.1-flash", session=s, sleep=lambda _: None), s


TRIAGE = {"category": "fix", "reason": "r", "reply": "好的", "confidence": 0.9, "labels": ["bug"]}


@pytest.mark.parametrize("text", [
    json.dumps(TRIAGE),
    "```json\n" + json.dumps(TRIAGE) + "\n```",
    "结论如下：" + json.dumps(TRIAGE) + " 以上",
])
def test_extract_json(text):
    assert extract_json(text) == TRIAGE


def test_extract_json_rejects_garbage():
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_chat_json_retries_once_with_error_feedback():
    llm, s = make(ok('{"category": "merge_now"}'), ok(json.dumps(TRIAGE)))
    assert llm.chat_json("sys", "user", schemas.ISSUE_TRIAGE) == TRIAGE
    assert len(s.bodies) == 2
    assert "不合法" in s.bodies[1]["messages"][-1]["content"]
    # Go 订阅要求：同一段对话复用同一个 session，且带自定义 User-Agent
    sids = {h["x-opencode-session"] for h in s.sent_headers}
    assert len(sids) == 1
    assert s.headers["User-Agent"].startswith("isaachuo-workflow-bot/")


def test_chat_json_gives_up_after_two_invalid():
    llm, _ = make(ok("{}"), ok('{"category": "x"}'))
    with pytest.raises(LLMOutputError):
        llm.chat_json("sys", "user", schemas.ISSUE_TRIAGE)


def test_retries_on_429_and_5xx():
    llm, s = make(Resp(429, text="slow"), Resp(502, text="bad"), ok("{}"))
    assert llm.chat([{"role": "user", "content": "x"}]) == "{}"
    assert len(s.bodies) == 3
    assert s.bodies[0]["model"] == "deepseek-v4.1-flash"


def test_falls_back_when_response_format_unsupported():
    llm, s = make(Resp(400, text="response_format not supported"), ok("{}"))
    llm.chat([{"role": "user", "content": "x"}])
    assert "response_format" not in s.bodies[-1]
    assert llm.json_mode is False


def test_client_error_raises():
    llm, _ = make(Resp(401, text="bad key"))
    with pytest.raises(LLMError):
        llm.chat([{"role": "user", "content": "x"}])


def test_missing_key():
    with pytest.raises(LLMError):
        LLM("", "https://x", "m")
