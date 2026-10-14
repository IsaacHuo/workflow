"""跨轮次状态：control 仓库 bot-state 孤立分支上的 state.json。

用 Contents API 的 sha 做乐观锁，多个 loop 同时写时冲突重试。
结构：
  violations: {login: [{repo, pr, ts, reason}]}
  pending_lessons: [{category, text, source, ts}]
  cursors: {key: value}
  events: [{ts, loop, repo, kind, target, detail}]
"""
from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timezone
from typing import Callable

from .github import GitHub, GitHubError

log = logging.getLogger(__name__)
PATH = "state.json"
MAX_EVENTS = 1000


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty() -> dict:
    return {"violations": {}, "pending_lessons": [], "cursors": {}, "events": []}


class StateStore:
    def __init__(self, gh: GitHub, repo: str, branch: str):
        self.gh, self.repo, self.branch = gh, repo, branch

    def load(self) -> tuple[dict, str | None]:
        if self.gh.get_ref(self.repo, self.branch) is None:
            return empty(), None
        got = self.gh.get_file(self.repo, PATH, self.branch)
        if got is None:
            return empty(), None
        text, sha = got
        data = {**empty(), **json.loads(text)}
        return data, sha

    def update(self, mutate: Callable[[dict], None], message: str, attempts: int = 5) -> dict:
        for _ in range(attempts):
            data, sha = self.load()
            new = copy.deepcopy(data)
            mutate(new)
            new["events"] = new["events"][-MAX_EVENTS:]
            if new == data:
                return data
            text = json.dumps(new, ensure_ascii=False, indent=1, sort_keys=True)
            try:
                if self.gh.get_ref(self.repo, self.branch) is None:
                    self.gh.create_orphan_branch(self.repo, self.branch, PATH, text, message)
                else:
                    self.gh.put_file(self.repo, PATH, text, message, self.branch, sha)
                return new
            except GitHubError as e:
                if e.status in (409, 422):
                    log.warning("state 冲突，重试: %s", e)
                    continue
                raise
        raise RuntimeError("state 写入多次冲突")


class Recorder:
    """收集本轮的事件 / 违规 / 经验，最后一次性写入 state。"""

    def __init__(self, loop: str):
        self.loop = loop
        self.events: list[dict] = []
        self.violations: list[tuple[str, dict]] = []
        self.lessons: list[dict] = []
        self.cursors: dict[str, str] = {}

    def event(self, repo: str, kind: str, target: str, detail: str = ""):
        self.events.append({"ts": now_iso(), "loop": self.loop, "repo": repo, "kind": kind,
                            "target": target, "detail": detail[:300]})

    def violation(self, login: str, repo: str, pr: int, reason: str):
        self.violations.append((login.lower(), {"repo": repo, "pr": pr, "ts": now_iso(), "reason": reason[:300]}))

    def lesson(self, category: str, text: str, source: str):
        self.lessons.append({"category": category, "text": text[:600], "source": source, "ts": now_iso()})

    def apply(self, state: dict):
        state["events"].extend(self.events)
        for login, v in self.violations:
            lst = state["violations"].setdefault(login, [])
            if not any(x["repo"] == v["repo"] and x["pr"] == v["pr"] for x in lst):
                lst.append(v)
        state["pending_lessons"].extend(self.lessons)
        state["cursors"].update(self.cursors)

    def empty(self) -> bool:
        return not (self.events or self.violations or self.lessons or self.cursors)

    def flush(self, store: StateStore) -> dict | None:
        if self.empty():
            return None
        if store.gh.dry_run:
            log.info("[DRY_RUN] state 不写入：%d 事件 %d 违规 %d 经验", len(self.events), len(self.violations),
                     len(self.lessons))
            data, _ = store.load()
            self.apply(data)
            return data
        return store.update(self.apply, f"state: {self.loop}")
