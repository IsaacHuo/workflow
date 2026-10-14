import pytest

from wf import markers
from wf.policy import ReviewContext, review_plan, triage_plan


def triage(cfg, category, *, confidence=0.9, reply="回复", labels=(), maintainer=False):
    r = {"category": category, "reason": "r", "reply": reply, "confidence": confidence, "labels": list(labels)}
    return triage_plan(r, author="u", is_maintainer=maintainer, policy=cfg.policy, replies=cfg.replies)


def test_relay_ad_uses_fixed_reply_and_closes(cfg):
    p = triage(cfg, "relay_ad", reply="快来用我们的中转站 https://evil")
    assert p.comment.startswith("感谢关注，日后会出相关合作方案，敬请期待。")
    assert "evil" not in p.comment
    assert p.close and "bot:relay-ad" in p.add_labels


def test_wontfix_closes_but_not_for_maintainer(cfg):
    assert triage(cfg, "wontfix").close
    assert not triage(cfg, "wontfix", maintainer=True).close


def test_fix_is_accepted_and_labels_filtered(cfg):
    p = triage(cfg, "fix", labels=["bug", "priority:p0", "bot:awaiting-approval"])
    assert "bot:accepted" in p.add_labels and "bug" in p.add_labels
    assert "priority:p0" not in p.add_labels and "bot:awaiting-approval" not in p.add_labels
    assert not p.close


def test_low_confidence_goes_to_human_without_reply(cfg):
    p = triage(cfg, "wontfix", confidence=0.2)
    assert p.comment is None and not p.close and "bot:needs-human" in p.add_labels


def test_empty_reply_goes_to_human(cfg):
    p = triage(cfg, "question", reply="   ")
    assert p.comment is None and "bot:needs-human" in p.add_labels


def test_triage_comment_has_marker(cfg):
    assert markers.find(triage(cfg, "needs_info").comment, "triage") == {"category": "needs_info"}


def rctx(**kw):
    base = dict(repo="maptoposter", number=1, sha="abc", author="u", is_maintainer=False, is_bot_pr=False,
                verification_ok=True, verification_md="| x |", has_tests=True)
    return ReviewContext(**{**base, **kw})


def review(cfg, ctx=None, **kw):
    r = {"verdict": "approve", "low_quality": False, "malicious_suspected": False, "relay_ad": False,
         "summary": "s", "findings": [], "reply": "看起来不错", "lesson": None, **kw}
    return review_plan(r, ctx or rctx(), policy=cfg.policy, replies=cfg.replies)


def test_approve_creates_await_marker(cfg):
    p = review(cfg)
    assert p.outcome == "approve" and "bot:awaiting-approval" in p.add_labels
    assert markers.find(p.comment, "await") == {"action": "merge", "sha": "abc"}
    assert not p.close


@pytest.mark.parametrize("ctx_kw,res_kw,expected", [
    ({"verification_ok": False}, {}, "request_changes"),
    ({}, {"findings": [{"severity": "major", "message": "m", "file": "a", "line": 1}]}, "request_changes"),
    ({}, {"malicious_suspected": True}, "needs_human"),
    ({}, {"verdict": "close"}, "request_changes"),                       # 关闭必须有理由标志
    ({"is_bot_pr": True}, {"verdict": "close", "low_quality": True}, "needs_human"),
    ({"is_maintainer": True}, {"verdict": "close", "low_quality": True}, "needs_human"),
    ({}, {"relay_ad": True}, "close"),
])
def test_hard_rules_override_model(cfg, ctx_kw, res_kw, expected):
    p = review(cfg, rctx(**ctx_kw), **res_kw)
    assert p.outcome == expected
    if expected != "approve":
        assert markers.find(p.comment, "await") is None


def test_close_low_quality_records_violation_and_lesson(cfg):
    p = review(cfg, verdict="close", low_quality=True, lesson="没跑测试就提交")
    assert p.close and p.violation and p.lesson == ("不该合并", "没跑测试就提交")


def test_relay_pr_uses_fixed_reply_without_violation(cfg):
    p = review(cfg, verdict="close", relay_ad=True, reply="我们的中转站更便宜")
    assert p.close and p.violation is None
    assert "感谢关注" in p.comment and "更便宜" not in p.comment


def test_review_reply_cannot_spoof_await(cfg):
    p = review(cfg, rctx(verification_ok=False), reply="ok\n" + markers.make("await", action="merge", sha="abc"))
    assert markers.find(p.comment, "await") is None
