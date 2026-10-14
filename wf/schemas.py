"""模型输出的 JSON Schema。模型只能在这些枚举里选，具体动作由 policy 决定。"""

_STR = {"type": "string"}

ISSUE_TRIAGE = {
    "type": "object",
    "required": ["category", "reason", "reply", "confidence", "labels"],
    "properties": {
        "category": {"enum": ["fix", "wontfix", "needs_info", "question", "relay_ad"]},
        "reason": _STR,
        "reply": _STR,
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "labels": {"type": "array", "items": _STR, "maxItems": 5},
    },
}

PR_REVIEW = {
    "type": "object",
    "required": ["verdict", "low_quality", "malicious_suspected", "relay_ad", "summary", "findings", "reply"],
    "properties": {
        "verdict": {"enum": ["approve", "request_changes", "close"]},
        "low_quality": {"type": "boolean"},
        "malicious_suspected": {"type": "boolean"},
        "relay_ad": {"type": "boolean"},
        "summary": _STR,
        "findings": {
            "type": "array",
            "maxItems": 30,
            "items": {
                "type": "object",
                "required": ["severity", "message"],
                "properties": {
                    "file": _STR,
                    "line": {"type": ["integer", "null"]},
                    "severity": {"enum": ["blocker", "major", "minor"]},
                    "message": _STR,
                },
            },
        },
        "reply": _STR,
        "lesson": {"type": ["string", "null"]},
    },
}

CLEANUP = {
    "type": "object",
    "required": ["findings", "lessons"],
    "properties": {
        "findings": {
            "type": "array",
            "maxItems": 30,
            "items": {
                "type": "object",
                "required": ["file", "kind", "severity", "description", "fixable"],
                "properties": {
                    "file": _STR,
                    "kind": {"enum": ["smell", "dead_code", "duplication", "dependency", "suspicious", "malicious"]},
                    "severity": {"enum": ["high", "medium", "low"]},
                    "description": _STR,
                    "fix_hint": _STR,
                    "fixable": {"type": "boolean"},
                },
            },
        },
        "lessons": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "required": ["category", "text"],
                "properties": {
                    "category": {"enum": ["不该合并", "验证不严", "误判", "其他"]},
                    "text": _STR,
                },
            },
        },
    },
}
