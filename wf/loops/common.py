"""各 loop 共用的启动代码。"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import Config, Runtime, load_config
from ..github import GitHub
from ..state import Recorder, StateStore

log = logging.getLogger("wf")


@dataclass
class Ctx:
    cfg: Config
    rt: Runtime
    gh: GitHub
    store: StateStore
    rec: Recorder


def setup(loop: str, need_github: bool = True) -> Ctx:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    rt = Runtime.from_env()
    if need_github and not os.environ.get("GH_TOKEN"):
        raise SystemExit("缺少 GH_TOKEN（GitHub App 令牌）")
    if need_github and not rt.bot_login:
        raise SystemExit("缺少 BOT_LOGIN")
    gh = GitHub(os.environ.get("GH_TOKEN", ""), dry_run=rt.dry_run)
    store = StateStore(gh, cfg.control_repo, cfg.bot["state_branch"])
    log.info("loop=%s dry_run=%s bot=%s", loop, rt.dry_run, rt.bot_login)
    return Ctx(cfg, rt, gh, store, Recorder(loop))


def label_names(item: dict) -> set[str]:
    return {lb["name"] for lb in item.get("labels", [])}


def is_maintainer(cfg: Config, login: str) -> bool:
    return login.lower() == cfg.maintainer.lower()


def step_summary(lines: list[str]):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    text = "\n".join(lines) + "\n"
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)


def set_output(name: str, value):
    v = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}<<__WF_EOF__\n{v}\n__WF_EOF__\n")
    else:
        print(f"{name}={v}")


def write_json(path: str | Path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def read_json(path: str | Path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def git(*args: str, cwd: str | Path, check: bool = True, env: dict | None = None) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env={**os.environ, **(env or {})})
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args[0]} 失败: {r.stderr.strip()[:500]}")
    return r.stdout


def git_ok(*args: str, cwd: str | Path) -> bool:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True).returncode == 0
