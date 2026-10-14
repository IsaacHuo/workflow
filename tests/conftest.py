from __future__ import annotations

import itertools

import pytest

from wf.config import Runtime, load_config
from wf.loops.common import Ctx
from wf.state import Recorder

BOT = "wf-isaac[bot]"
OWNER = "IsaacHuo"
_ids = itertools.count(1)


def comment(login: str, body: str, ts: str, assoc: str = "NONE") -> dict:
    return {"id": next(_ids), "user": {"login": login}, "body": body, "created_at": ts, "author_association": assoc}


class FakeGH:
    """够用的内存版 GitHub，记录所有写操作。"""

    dry_run = False

    def __init__(self):
        self.issues: dict[tuple[str, int], dict] = {}
        self.comments: dict[tuple[str, int], list[dict]] = {}
        self.pulls: dict[tuple[str, int], dict] = {}
        self.releases: dict[str, list[dict]] = {}
        self.tags: dict[str, list[str]] = {}
        self.files: dict[tuple[str, str], tuple[str, str]] = {}
        self.diffs: dict[tuple[str, int], str] = {}
        self.ci: dict[str, list[dict]] = {}
        self.calls: list[tuple] = []

    # ---- 构造数据
    def add_issue(self, repo, n, *, title="t", body="", author="someone", labels=(), is_pr=False, ts="2026-10-01T00:00:00Z"):
        it = {"number": n, "title": title, "body": body, "user": {"login": author}, "labels": [{"name": x} for x in labels],
              "state": "open", "created_at": ts, "html_url": f"https://github.com/{repo}/issues/{n}"}
        if is_pr:
            it["pull_request"] = {}
        self.issues[(repo, n)] = it
        self.comments.setdefault((repo, n), [])
        return it

    def add_pr(self, repo, n, *, sha="abc", author="someone", labels=(), body="", title="t"):
        it = self.add_issue(repo, n, author=author, labels=labels, is_pr=True, body=body, title=title)
        pr = {**it, "head": {"sha": sha, "ref": f"feature-{n}", "label": f"{author}:feature"},
              "base": {"ref": "main", "repo": {"full_name": repo}}, "draft": False, "additions": 1, "deletions": 1}
        self.pulls[(repo, n)] = pr
        return pr

    def labels_of(self, repo, n):
        return {x["name"] for x in self.issues[(repo, n)]["labels"]}

    def writes(self, kind=None):
        return [c for c in self.calls if kind is None or c[0] == kind]

    # ---- 读
    def get(self, path, **kw):
        return {"description": "desc"}

    def paginate(self, path, params=None, limit=1000):
        repo = "/".join(path.split("/")[2:4])
        label = (params or {}).get("labels")
        for (r, _n), it in self.issues.items():
            if r == repo and (not label or label in {x["name"] for x in it["labels"]}):
                yield it

    def list_open_issues(self, repo, labels=None):
        return [i for (r, _), i in self.issues.items() if r == repo and i["state"] == "open" and "pull_request" not in i
                and (not labels or labels in {x["name"] for x in i["labels"]})]

    def list_open_items(self, repo, labels):
        return [i for (r, _), i in self.issues.items() if r == repo and i["state"] == "open"
                and labels in {x["name"] for x in i["labels"]}]

    def list_open_pulls(self, repo):
        return [p for (r, _), p in self.pulls.items() if r == repo and self.issues[(r, p["number"])]["state"] == "open"]

    def get_pull(self, repo, n):
        pr = self.pulls[(repo, n)]
        return {**pr, "state": self.issues[(repo, n)]["state"], "labels": self.issues[(repo, n)]["labels"]}

    def list_comments(self, repo, n):
        return list(self.comments.get((repo, n), []))

    def pull_files(self, repo, n):
        return ["src/a.py"]

    def pull_diff(self, repo, n):
        return self.diffs.get((repo, n), "diff --git a/src/a.py b/src/a.py\n+print(1)\n")

    def check_runs(self, repo, sha):
        return self.ci.get(sha, [])

    def list_tags(self, repo):
        return self.tags.get(repo, [])

    def list_releases(self, repo):
        return self.releases.get(repo, [])

    def get_file(self, repo, path, ref):
        return self.files.get((repo, path))

    def get_ref(self, repo, branch):
        return "base-sha"

    # ---- 写
    def comment(self, repo, n, body):
        self.calls.append(("comment", repo, n, body))
        c = comment(BOT, body, "2026-10-14T12:00:00Z", "NONE")
        self.comments.setdefault((repo, n), []).append(c)
        return c

    def add_labels(self, repo, n, labels):
        if labels:
            self.calls.append(("add_labels", repo, n, tuple(labels)))
            have = self.labels_of(repo, n)
            self.issues[(repo, n)]["labels"] += [{"name": x} for x in labels if x not in have]

    def remove_label(self, repo, n, label):
        self.calls.append(("remove_label", repo, n, label))
        self.issues[(repo, n)]["labels"] = [x for x in self.issues[(repo, n)]["labels"] if x["name"] != label]

    def close_issue(self, repo, n, reason="not_planned"):
        self.calls.append(("close_issue", repo, n, reason))
        self.issues[(repo, n)]["state"] = "closed"

    def close_pull(self, repo, n):
        self.calls.append(("close_pull", repo, n))
        self.issues[(repo, n)]["state"] = "closed"

    def merge_pull(self, repo, n, sha, method):
        self.calls.append(("merge_pull", repo, n, sha, method))
        self.issues[(repo, n)]["state"] = "closed"
        return {"merged": True, "sha": "merge-sha"}

    def create_issue(self, repo, title, body, labels=None):
        n = 1000 + len(self.writes("create_issue"))
        self.calls.append(("create_issue", repo, title, body, tuple(labels or ())))
        it = self.add_issue(repo, n, title=title, body=body, author=BOT, labels=labels or ())
        return {"number": n, "html_url": it["html_url"]}

    def create_release(self, repo, **body):
        self.calls.append(("create_release", repo, body))
        rel = {"id": 1, "html_url": "https://rel", **body}
        self.releases.setdefault(repo, []).append(rel)
        return rel

    def update_release(self, repo, rid, **body):
        self.calls.append(("update_release", repo, rid, body))
        for r in self.releases.get(repo, []):
            if r["id"] == rid:
                r.update(body)
                return r
        return {}

    def put_file(self, repo, path, text, message, branch, sha):
        self.calls.append(("put_file", repo, path, text, branch))
        self.files[(repo, path)] = (text, "new-sha")
        return {"commit": {"sha": "c0ffee"}}

    def ensure_labels(self, repo, labels):
        pass


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts: list[tuple[str, str]] = []

    def chat_json(self, system, user, schema):
        import jsonschema

        self.prompts.append((system, user))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        jsonschema.validate(r, schema)
        return r


class FakeStore:
    def __init__(self):
        self.gh = FakeGH()
        from wf.state import empty

        self.data = empty()

    def load(self):
        return self.data, "sha"

    def update(self, mutate, message):
        mutate(self.data)
        return self.data


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def ctx(cfg):
    gh = FakeGH()
    store = FakeStore()
    return Ctx(cfg=cfg, rt=Runtime(dry_run=False, bot_login=BOT, run_url=""), gh=gh, store=store, rec=Recorder("test"))
