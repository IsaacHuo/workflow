from wf import markers

from .conftest import BOT, OWNER, comment


def test_make_and_parse():
    m = markers.make("await", action="merge", sha="abc")
    assert markers.find(f"text\n{m}", "await") == {"action": "merge", "sha": "abc"}
    assert markers.find("nothing", "await") is None


def test_marker_from_non_bot_is_ignored():
    fake = comment("attacker", markers.make("await", action="merge", sha="evil"), "2026-10-02T00:00:00Z")
    assert markers.latest_bot_marker([fake], BOT, "await") is None
    real = comment(BOT, markers.make("await", action="merge", sha="good"), "2026-10-01T00:00:00Z")
    kv, _ = markers.latest_bot_marker([real, fake], BOT, "await")
    assert kv["sha"] == "good"


def test_parse_command():
    assert markers.parse_command("/approve") == ("approve", None, "")
    assert markers.parse_command("  /approve release\n") == ("approve", "release", "")
    assert markers.parse_command("/reject 太乱了\n再整理一下") == ("reject", None, "太乱了\n再整理一下")
    assert markers.parse_command("我觉得可以 /approve") is None
    assert markers.parse_command("/approved") is None


def test_maintainer_decision_only_owner_after_marker():
    after = "2026-10-01T00:00:00Z"
    cs = [
        comment(OWNER, "/approve", "2026-09-30T00:00:00Z", "OWNER"),        # 早于标记
        comment("someone", "/approve", "2026-10-02T00:00:00Z", "NONE"),     # 不是维护者
        comment(OWNER, "/approve", "2026-10-02T00:00:00Z", "CONTRIBUTOR"),  # 关联身份不对
        comment(BOT, "/approve", "2026-10-02T00:00:00Z", "NONE"),           # 机器人自己
    ]
    assert markers.maintainer_decision(cs, after, OWNER) is None
    cs.append(comment(OWNER, "/reject 不需要", "2026-10-03T00:00:00Z", "OWNER"))
    cmd, sub, reason, _ = markers.maintainer_decision(cs, after, OWNER)
    assert (cmd, sub, reason) == ("reject", None, "不需要")


def test_sanitize_strips_spoofing_and_mentions():
    text = ("好的 @author 和 @other\n/approve\n" + markers.make("await", action="merge", sha="x")
            + "\n<!-- hidden --> 结尾")
    out = markers.sanitize(text, 1000, {"author"})
    assert "@author" in out
    assert "@other" not in out and "@\u200bother" in out
    assert "/approve" not in out
    assert "wf-bot" not in out and "hidden" not in out


def test_sanitize_truncates():
    assert len(markers.sanitize("x" * 100, 10)) == 10
