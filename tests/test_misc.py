from wf import release
from wf.changes import select_checks
from wf.lessons import merge_lessons
from wf.patches import problems
from wf.state import Recorder, empty

from .conftest import BOT, FakeGH


def test_config_loads(cfg):
    assert set(cfg.repos) == {"maptoposter", "Myleafy", "familiar"}
    assert cfg.repo("myleafy").release_mode == "none"
    assert cfg.repo("IsaacHuo/maptoposter").full_name == "IsaacHuo/maptoposter"
    for r in cfg.repos.values():
        assert r.checks, r.name


def test_select_checks(cfg):
    mp = cfg.repo("maptoposter")
    assert [c.id for c in select_checks(mp, ["src/maptoposter/cli.py"])] == ["python"]
    assert [c.id for c in select_checks(mp, ["frontend/src/App.tsx"])] == ["python", "frontend"]
    assert len(select_checks(mp, [".github/workflows/x.yml"])) == 2
    fam = cfg.repo("familiar")
    assert select_checks(fam, ["README.md"]) == []
    assert [c.id for c in select_checks(fam, ["Familiar/App/FamiliarApp.swift"])] == ["ios"]


def test_next_version():
    assert release.next_version([], "v0.2.0") == "v0.2.0"
    assert release.next_version(["v2.8", "v3.0", "v2.9", "nightly"], "v1.0.0") == "v3.0.1"
    assert release.next_version(["1.2.3", "1.10.0"], "v0") == "1.10.1"


def test_add_to_draft_creates_then_appends(cfg):
    gh = FakeGH()
    repo = cfg.repo("maptoposter")
    gh.tags[repo.full_name] = ["v0.2.0"]
    d = release.add_to_draft(gh, repo, "sha1", "fix A (#1)")
    assert d["tag_name"] == "v0.2.1" and d["draft"] is True
    d2 = release.add_to_draft(gh, repo, "sha2", "fix B (#2)")
    assert "fix A" in d2["body"] and "fix B" in d2["body"] and d2["target_commitish"] == "sha2"
    assert len(gh.writes("create_release")) == 1


def test_tracking_issue_not_duplicated(cfg):
    gh = FakeGH()
    repo = cfg.repo("maptoposter")
    draft = {"tag_name": "v0.2.1", "html_url": "u"}
    release.ensure_tracking_issue(gh, cfg.control_repo, repo, draft, BOT)
    release.ensure_tracking_issue(gh, cfg.control_repo, repo, draft, BOT)
    assert len(gh.writes("create_issue")) == 1


def test_merge_lessons_into_sections():
    md = "# 经验\n\n## 不该合并\n\n- 旧条目\n\n## 误判\n\n## 其他\n"
    out = merge_lessons(md, [
        {"category": "不该合并", "text": "新条目 A", "source": "x#1", "ts": "2026-10-14T00:00:00Z"},
        {"category": "误判", "text": "新条目 B", "source": "x#2", "ts": "2026-10-14T00:00:00Z"},
        {"category": "验证不严", "text": "新条目 C", "source": "x#3", "ts": "2026-10-14T00:00:00Z"},
        {"category": "不该合并", "text": "旧条目", "source": "dup", "ts": ""},
    ])
    sec = out.split("## ")
    assert "新条目 A" in sec[1] and "旧条目" in sec[1]
    assert "新条目 B" in sec[2]
    assert out.count("旧条目") == 1
    assert "## 验证不严" in out and out.index("新条目 C") > out.index("## 验证不严")


def test_recorder_dedupes_violations():
    rec = Recorder("t")
    rec.violation("Spammer", "maptoposter", 3, "x")
    rec.violation("spammer", "maptoposter", 3, "x")
    s = empty()
    rec.apply(s)
    rec.apply(s)
    assert len(s["violations"]["spammer"]) == 1


def test_patch_problems():
    patch = "diff --git a/x b/x\n+ok\n"
    assert problems(patch, ["src/x.py"], "改了 x", [".github/"], ["sekret"]) == []
    assert problems("", [], "", [".github/"], [])                                       # 空补丁
    assert problems(patch, [".github/workflows/ci.yml"], "", [".github/"], [])          # 禁止路径
    assert problems(patch + "+key=sekret\n", ["x"], "", [], ["sekret"])                 # 泄露密钥值
    assert problems(patch + "+t=ghp_" + "a" * 30 + "\n", ["x"], "", [], [])             # 令牌格式
    assert problems(patch, ["x"], "CANNOT_FIX 信息不足", [], [])
