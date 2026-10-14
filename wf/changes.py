"""按改动路径挑选要在沙箱里跑的 check，生成 Actions matrix 条目。"""
from __future__ import annotations

from .config import Check, Repo


def select_checks(repo: Repo, files: list[str]) -> list[Check]:
    if any(f.startswith(p) for f in files for p in repo.force_all_prefixes):
        return list(repo.checks)
    return [c for c in repo.checks if not c.paths or any(f.startswith(p) for f in files for p in c.paths)]


def sensitive(files: list[str], prefixes: list[str]) -> list[str]:
    return [f for f in files if any(f.startswith(p) for p in prefixes)]


def matrix_entry(repo: Repo, number: int, sha: str, check: Check) -> dict:
    t = check.tools
    return {
        "key": f"{repo.name}-{number}-{check.id}",
        "repo": repo.full_name,
        "number": number,
        "sha": sha,
        "check": check.id,
        "runs_on": check.runs_on,
        "run": check.run,
        "node": str(t.get("node", "")),
        "java": str(t.get("java", "")),
        "python": str(t.get("python", "")),
        "uv": bool(t.get("uv", False)),
        "submodules": repo.submodules,
        "lfs": repo.lfs,
    }
