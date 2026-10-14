"""收集 / 校验 / 推送 opencode 产出的补丁。

collect 在持有模型密钥但没有 GitHub 写权限的 job 里跑；push 在持有 App 令牌但不跑任何外部代码的 job 里跑。
"""
from __future__ import annotations

import base64
import re
import subprocess
import tempfile
from pathlib import Path

from .loops.common import git

SUMMARY_FILE = ".wf-summary.md"
MAX_PATCH_BYTES = 500_000
SECRET_PATTERNS = re.compile(
    r"(gh[pousr]_[0-9A-Za-z_]{20,}|github_pat_[0-9A-Za-z_]{20,}|oc_sk_[0-9A-Za-z_]{16,}|sk-[A-Za-z0-9_-]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


def write_patch(path: Path, text: str):
    """补丁按字节原样写（不做换行转换、保留非 UTF-8 字节），否则 CRLF 文件的补丁会被写坏。"""
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(text)


def read_patch(path: Path) -> str:
    if not path.exists():
        return ""
    with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
        return f.read()


def collect(workdir: Path) -> tuple[str, list[str], str]:
    summary_path = workdir / SUMMARY_FILE
    summary = summary_path.read_text(encoding="utf-8", errors="replace") if summary_path.exists() else ""
    if summary_path.exists():
        summary_path.unlink()
    git("add", "-A", cwd=workdir)
    raw = subprocess.run(["git", "diff", "--cached", "--binary"], cwd=workdir, capture_output=True, check=True).stdout
    patch = raw.decode("utf-8", errors="surrogateescape")
    files = [f for f in git("diff", "--cached", "--name-only", cwd=workdir).splitlines() if f]
    return patch, files, summary.strip()


def problems(patch: str, files: list[str], summary: str, forbidden_prefixes: list[str],
             secret_values: list[str]) -> list[str]:
    out = []
    if summary.startswith("CANNOT_FIX"):
        out.append("模型判断无法安全修复：" + summary[len("CANNOT_FIX"):].strip()[:500])
    if not patch.strip():
        out.append("没有产生任何改动")
    if len(patch.encode("utf-8", errors="surrogateescape")) > MAX_PATCH_BYTES:
        out.append(f"补丁过大（{len(patch)} 字符）")
    bad = [f for f in files if any(f.startswith(p) for p in forbidden_prefixes)]
    if bad:
        out.append("改动了禁止自动修改的路径：" + ", ".join(bad[:10]))
    added = "\n".join(ln for ln in patch.splitlines() if ln.startswith("+"))
    if any(v and v in patch for v in secret_values) or SECRET_PATTERNS.search(added):
        out.append("补丁里出现疑似密钥，已丢弃")
    return out


def push_branch(token: str, repo_full: str, base: str, branch: str, patch: str, message: str,
                author_name: str, author_email: str) -> str:
    """在临时目录 clone → apply → commit → push。返回新提交 SHA。令牌只放在 http header 里，不进 URL。"""
    header = "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
    auth = ["-c", f"http.https://github.com/.extraheader={header}"]
    env = {"GIT_AUTHOR_NAME": author_name, "GIT_AUTHOR_EMAIL": author_email,
           "GIT_COMMITTER_NAME": author_name, "GIT_COMMITTER_EMAIL": author_email,
           "GIT_TERMINAL_PROMPT": "0"}
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "repo"
        git(*auth, "clone", "--quiet", "--depth", "1", "--branch", base, f"https://github.com/{repo_full}.git",
            str(work), cwd=tmp, env=env)
        write_patch(Path(tmp) / "change.patch", patch)
        git("apply", "--index", "--whitespace=nowarn", str(Path(tmp) / "change.patch"), cwd=work)
        git("checkout", "-q", "-b", branch, cwd=work)
        git("commit", "-q", "-m", message, cwd=work, env=env)
        sha = git("rev-parse", "HEAD", cwd=work).strip()
        git(*auth, "push", "-q", "origin", f"HEAD:refs/heads/{branch}", cwd=work, env=env)
        return sha
