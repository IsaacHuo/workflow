"""守住 workflow YAML 里的安全边界。"""
from pathlib import Path

import pytest
import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"
FILES = sorted(WF.glob("*.yml"))


def load(p):
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def test_workflows_parse():
    assert FILES
    for p in FILES:
        assert "jobs" in load(p), p.name


@pytest.mark.parametrize("p", [f for f in FILES if f.name != "ci.yml"], ids=lambda p: p.name)
def test_default_permissions_empty(p):
    assert load(p)["permissions"] == {}


def test_sandbox_has_no_credentials():
    job = load(WF / "pr-loop.yml")["jobs"]["sandbox"]
    assert job["permissions"] == {}
    text = yaml.safe_dump(job)
    assert "secrets." not in text and "vars." not in text and "bot-setup" not in text
    assert "token" not in text.lower()


def test_untrusted_pr_code_only_fetched_in_sandbox():
    for p in FILES:
        for name, job in load(p)["jobs"].items():
            text = yaml.safe_dump(job)
            if "pull/${NUMBER}/head" in text:
                assert (p.name, name) == ("pr-loop.yml", "sandbox")


def test_jobs_with_model_key_have_no_github_write_token():
    """持有模型密钥又跑 opencode 的 job 不能拿 App 令牌。"""
    for p in FILES:
        for name, job in load(p)["jobs"].items():
            text = yaml.safe_dump(job)
            if "opencode run" in text and p.name != "smoke.yml":
                assert "bot-setup" not in text and "GH_TOKEN" not in text, (p.name, name)
                assert job.get("permissions") == {"contents": "read"}


def test_no_cache_restore_in_privileged_jobs():
    """沙箱可写 Actions cache，特权 job 不能恢复任何 cache，防止缓存投毒。"""
    for p in FILES:
        for name, job in load(p)["jobs"].items():
            if name == "sandbox":
                continue
            text = yaml.safe_dump(job)
            assert "actions/cache" not in text, (p.name, name)
            for step in job.get("steps", []):
                assert "cache" not in (step.get("with") or {}), (p.name, name, step)


def test_bot_loops_gated_by_switch():
    for p in FILES:
        if p.name in ("ci.yml", "smoke.yml"):
            continue
        jobs = load(p)["jobs"]
        first = next(iter(jobs.values()))
        assert first.get("if") == "vars.BOT_ENABLED == 'true'", p.name
