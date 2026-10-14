"""读取 config/*.yml 和运行时环境变量。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def _load(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass(frozen=True)
class Check:
    id: str
    runs_on: str
    run: str
    paths: tuple[str, ...] = ()
    tools: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Repo:
    name: str
    full_name: str
    default_branch: str
    has_tests: bool
    release_mode: str
    first_version: str
    merge_method: str
    submodules: bool
    lfs: bool
    force_all_prefixes: tuple[str, ...]
    checks: tuple[Check, ...]


@dataclass
class Config:
    bot: dict
    repos: dict[str, Repo]
    policy: dict
    replies: dict
    blacklist: set[str]

    @property
    def owner(self) -> str:
        return self.bot["owner"]

    @property
    def maintainer(self) -> str:
        return self.bot["maintainer"]

    @property
    def control_repo(self) -> str:
        return self.bot["control_repo"]

    def repo(self, name_or_full: str) -> Repo:
        name = name_or_full.split("/")[-1]
        for key, r in self.repos.items():
            if key.lower() == name.lower():
                return r
        raise KeyError(name_or_full)


def _parse_repos(raw: dict, owner: str) -> dict[str, Repo]:
    defaults = raw.get("defaults", {})
    out: dict[str, Repo] = {}
    for r in raw["repos"]:
        rel = r.get("release", {}) or {}
        checks = tuple(
            Check(
                id=c["id"],
                runs_on=c["runs_on"],
                run=c["run"],
                paths=tuple(c.get("paths") or ()),
                tools=dict(c.get("tools") or {}),
            )
            for c in r.get("checks", [])
        )
        out[r["name"]] = Repo(
            name=r["name"],
            full_name=f"{owner}/{r['name']}",
            default_branch=r.get("default_branch", "main"),
            has_tests=bool(r.get("has_tests", False)),
            release_mode=rel.get("mode", "none"),
            first_version=rel.get("first_version", "v0.1.0"),
            merge_method=r.get("merge_method", defaults.get("merge_method", "squash")),
            submodules=bool(r.get("submodules", False)),
            lfs=bool(r.get("lfs", False)),
            force_all_prefixes=tuple(r.get("force_all_prefixes", defaults.get("force_all_prefixes", ()))),
            checks=checks,
        )
    return out


def load_config() -> Config:
    bot = _load("bot.yml")
    bl = _load("blacklist.yml").get("users") or []
    logins = {(u["login"] if isinstance(u, dict) else str(u)).lower() for u in bl}
    return Config(
        bot=bot,
        repos=_parse_repos(_load("repos.yml"), bot["owner"]),
        policy=_load("policy.yml"),
        replies=_load("replies.yml"),
        blacklist=logins,
    )


@dataclass(frozen=True)
class Runtime:
    """运行时开关，全部来自环境变量。"""

    dry_run: bool
    bot_login: str
    run_url: str

    @classmethod
    def from_env(cls) -> "Runtime":
        return cls(
            dry_run=os.environ.get("DRY_RUN", "true").lower() != "false",
            bot_login=os.environ.get("BOT_LOGIN", ""),
            run_url=os.environ.get("RUN_URL", ""),
        )
