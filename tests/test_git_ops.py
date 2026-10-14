"""在真实的临时 git 仓库上验证补丁收集 / 应用和体检区间计算。"""
import shutil
import subprocess

import pytest

from wf import patches
from wf.loops.cleanup import diff_range
from wf.loops.common import git

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")
ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}


def init(path):
    path.mkdir()
    subprocess.run(["git", "-c", "init.templateDir=", "init", "-q", "-b", "main", str(path)], check=True)
    git("config", "core.autocrlf", "false", cwd=path)
    (path / "a.py").write_bytes(b"print(1)\n")
    (path / "crlf.txt").write_bytes(b"line1\r\nline2\r\n")
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", "init", cwd=path, env=ENV)
    return path


def test_collect_excludes_summary_and_applies_cleanly(tmp_path):
    work = init(tmp_path / "work")
    (work / "a.py").write_bytes(b"print(2)\n")
    (work / "crlf.txt").write_bytes(b"line1\r\nchanged\r\n")
    (work / "b.py").write_bytes(b"x = 1\n")
    (work / patches.SUMMARY_FILE).write_text("改了 a，新增 b", encoding="utf-8")
    patch, files, summary = patches.collect(work)
    assert sorted(files) == ["a.py", "b.py", "crlf.txt"] and summary == "改了 a，新增 b"
    assert patches.SUMMARY_FILE not in patch

    clean = init(tmp_path / "clean")
    patches.write_patch(tmp_path / "p.patch", patch)
    git("apply", "--index", str(tmp_path / "p.patch"), cwd=clean)
    for f in ("a.py", "b.py", "crlf.txt"):
        assert (clean / f).read_bytes() == (work / f).read_bytes(), f


def test_diff_range_uses_cursor_when_ancestor(tmp_path):
    repo = init(tmp_path / "r")
    first = git("rev-parse", "HEAD", cwd=repo).strip()
    (repo / "a.py").write_bytes(b"print(3)\n")
    git("commit", "-qam", "change", cwd=repo, env=ENV)
    head = git("rev-parse", "HEAD", cwd=repo).strip()
    assert diff_range(repo, first, 7) == (first, head)
    assert diff_range(repo, head, 7) is None                     # 游标已是最新
    assert diff_range(repo, "deadbeef" * 5, 7) is None           # 无效游标 + 7 天内全是新提交 → 无基线
