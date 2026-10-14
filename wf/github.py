"""GitHub REST 薄封装。所有写操作经过 _write，DRY_RUN 时只打印不执行。"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Iterator

import requests

log = logging.getLogger(__name__)
API = "https://api.github.com"


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub {status}: {message}")
        self.status = status


class GitHub:
    def __init__(self, token: str, dry_run: bool = True, session: requests.Session | None = None,
                 sleep=time.sleep):
        self.dry_run = dry_run
        self.s = session or requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "IsaacHuo-workflow-bot",
        })
        self._sleep = sleep
        self.writes: list[tuple[str, str, Any]] = []   # 记录本轮写操作（dry-run 也记录）

    # ---------- 底层 ----------
    def _request(self, method: str, path: str, *, params=None, body=None, accept: str | None = None,
                 ok_404: bool = False):
        url = path if path.startswith("http") else f"{API}{path}"
        headers = {"Accept": accept} if accept else None
        for attempt in range(4):
            r = self.s.request(method, url, params=params, json=body, headers=headers, timeout=60)
            if r.status_code in (502, 503, 504) or (
                r.status_code in (403, 429) and r.headers.get("X-RateLimit-Remaining") == "0"
            ):
                wait = min(60, 5 * 2 ** attempt)
                if r.headers.get("Retry-After", "").isdigit():
                    wait = int(r.headers["Retry-After"])
                elif r.headers.get("X-RateLimit-Reset", "").isdigit():
                    wait = max(1, int(r.headers["X-RateLimit-Reset"]) - int(time.time()))
                if wait > 120 or attempt == 3:
                    break                       # 限流要很久才恢复：直接失败，下一轮再来
                log.warning("GitHub %s %s -> %s, retry in %ss", method, path, r.status_code, wait)
                self._sleep(wait)
                continue
            break
        if r.status_code == 404 and ok_404:
            return None
        if r.status_code >= 400:
            raise GitHubError(r.status_code, r.text[:500])
        if r.status_code == 204 or not r.content:
            return {}
        if accept and "json" not in accept:
            return r.text
        return r.json()

    def get(self, path, **kw):
        return self._request("GET", path, **kw)

    def paginate(self, path: str, params: dict | None = None, limit: int = 1000) -> Iterator[dict]:
        params = {"per_page": 100, **(params or {})}
        page, n = 1, 0
        while True:
            items = self.get(path, params={**params, "page": page})
            if isinstance(items, dict):           # 有些接口包一层
                items = next((v for v in items.values() if isinstance(v, list)), [])
            for it in items:
                yield it
                n += 1
                if n >= limit:
                    return
            if len(items) < params["per_page"]:
                return
            page += 1

    def _write(self, method: str, path: str, body: Any = None, fake: Any = None):
        self.writes.append((method, path, body))
        if self.dry_run:
            log.info("[DRY_RUN] %s %s %s", method, path, json.dumps(body, ensure_ascii=False)[:300])
            return fake if fake is not None else {}
        return self._request(method, path, body=body)

    # ---------- issue / 评论 / label ----------
    def list_open_issues(self, repo: str, labels: str | None = None) -> list[dict]:
        params = {"state": "open"}
        if labels:
            params["labels"] = labels
        return [i for i in self.paginate(f"/repos/{repo}/issues", params) if "pull_request" not in i]

    def list_open_items(self, repo: str, labels: str) -> list[dict]:
        """issue 和 PR 都返回（issues 接口按 label 过滤时两者都有）。"""
        return list(self.paginate(f"/repos/{repo}/issues", {"state": "open", "labels": labels}))

    def get_issue(self, repo: str, number: int) -> dict:
        return self.get(f"/repos/{repo}/issues/{number}")

    def list_comments(self, repo: str, number: int) -> list[dict]:
        return list(self.paginate(f"/repos/{repo}/issues/{number}/comments"))

    def comment(self, repo: str, number: int, body: str) -> dict:
        return self._write("POST", f"/repos/{repo}/issues/{number}/comments", {"body": body},
                           fake={"id": 0, "body": body})

    def add_labels(self, repo: str, number: int, labels: list[str]):
        if labels:
            self._write("POST", f"/repos/{repo}/issues/{number}/labels", {"labels": labels})

    def remove_label(self, repo: str, number: int, label: str):
        try:
            self._write("DELETE", f"/repos/{repo}/issues/{number}/labels/{requests.utils.quote(label)}")
        except GitHubError as e:
            if e.status != 404:
                raise

    def close_issue(self, repo: str, number: int, reason: str = "not_planned"):
        self._write("PATCH", f"/repos/{repo}/issues/{number}", {"state": "closed", "state_reason": reason})

    def create_issue(self, repo: str, title: str, body: str, labels: list[str] | None = None) -> dict:
        return self._write("POST", f"/repos/{repo}/issues", {"title": title, "body": body, "labels": labels or []},
                           fake={"number": 0, "html_url": "(dry-run)"})

    def ensure_labels(self, repo: str, labels: dict[str, str]):
        existing = {lb["name"] for lb in self.paginate(f"/repos/{repo}/labels")}
        for name, color in labels.items():
            if name not in existing:
                self._write("POST", f"/repos/{repo}/labels", {"name": name, "color": color})

    # ---------- PR ----------
    def list_open_pulls(self, repo: str) -> list[dict]:
        return list(self.paginate(f"/repos/{repo}/pulls", {"state": "open"}))

    def get_pull(self, repo: str, number: int) -> dict:
        return self.get(f"/repos/{repo}/pulls/{number}")

    def pull_files(self, repo: str, number: int) -> list[str]:
        return [f["filename"] for f in self.paginate(f"/repos/{repo}/pulls/{number}/files", limit=3000)]

    def pull_diff(self, repo: str, number: int) -> str:
        return self.get(f"/repos/{repo}/pulls/{number}", accept="application/vnd.github.diff")

    def check_runs(self, repo: str, sha: str) -> list[dict]:
        """同名检查（push 和 pull_request 各触发一次、或重跑）只保留最新的一次。"""
        data = self.get(f"/repos/{repo}/commits/{sha}/check-runs", params={"per_page": 100})
        latest: dict[str, dict] = {}
        for c in sorted(data.get("check_runs", []), key=lambda c: c.get("started_at") or ""):
            latest[c["name"]] = {"name": c["name"], "status": c["status"], "conclusion": c.get("conclusion")}
        return list(latest.values())

    def merge_pull(self, repo: str, number: int, sha: str, method: str) -> dict:
        return self._write("PUT", f"/repos/{repo}/pulls/{number}/merge",
                           {"sha": sha, "merge_method": method}, fake={"merged": True, "sha": "dry-run-sha"})

    def close_pull(self, repo: str, number: int):
        self._write("PATCH", f"/repos/{repo}/pulls/{number}", {"state": "closed"})

    def create_pull(self, repo: str, head: str, base: str, title: str, body: str) -> dict:
        return self._write("POST", f"/repos/{repo}/pulls",
                           {"head": head, "base": base, "title": title, "body": body},
                           fake={"number": 0, "html_url": "(dry-run)"})

    # ---------- release / tag ----------
    def list_tags(self, repo: str) -> list[str]:
        return [t["name"] for t in self.paginate(f"/repos/{repo}/tags", limit=300)]

    def list_releases(self, repo: str) -> list[dict]:
        return list(self.paginate(f"/repos/{repo}/releases", limit=100))

    def create_release(self, repo: str, **body) -> dict:
        return self._write("POST", f"/repos/{repo}/releases", body,
                           fake={"id": 0, "html_url": "(dry-run)", **body})

    def update_release(self, repo: str, release_id: int, **body) -> dict:
        return self._write("PATCH", f"/repos/{repo}/releases/{release_id}", body,
                           fake={"id": release_id, "html_url": "(dry-run)", **body})

    # ---------- contents / refs ----------
    def get_file(self, repo: str, path: str, ref: str) -> tuple[str, str] | None:
        data = self.get(f"/repos/{repo}/contents/{path}", params={"ref": ref}, ok_404=True)
        if data is None:
            return None
        return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]

    def put_file(self, repo: str, path: str, text: str, message: str, branch: str, sha: str | None):
        body = {"message": message, "branch": branch,
                "content": base64.b64encode(text.encode("utf-8")).decode("ascii")}
        if sha:
            body["sha"] = sha
        return self._write("PUT", f"/repos/{repo}/contents/{path}", body)

    def get_ref(self, repo: str, branch: str) -> str | None:
        data = self.get(f"/repos/{repo}/git/ref/heads/{branch}", ok_404=True)
        return data["object"]["sha"] if data else None

    def create_branch(self, repo: str, branch: str, from_sha: str):
        self._write("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": from_sha})

    def create_orphan_branch(self, repo: str, branch: str, path: str, text: str, message: str):
        if self.dry_run:
            return self._write("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "orphan": path})
        tree = self._request("POST", f"/repos/{repo}/git/trees", body={
            "tree": [{"path": path, "mode": "100644", "type": "blob", "content": text}]})
        commit = self._request("POST", f"/repos/{repo}/git/commits",
                               body={"message": message, "tree": tree["sha"], "parents": []})
        self._write("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
