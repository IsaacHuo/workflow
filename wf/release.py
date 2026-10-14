"""发版：只打 tag + GitHub Release，不发任何包。

合并后维护一个 Release 草稿（带 wf-bot:release 标记）并在 control 仓库开追踪 issue；
维护者在追踪 issue 下 /approve 后发布草稿（发布时 GitHub 按 target_commitish 创建 tag）。
"""
from __future__ import annotations

import re

from . import markers
from .config import Repo
from .github import GitHub

_VER = re.compile(r"^(v?)(\d+)\.(\d+)(?:\.(\d+))?$")


def _key(tag: str):
    m = _VER.match(tag)
    return (int(m.group(2)), int(m.group(3)), int(m.group(4) or 0)) if m else None


def next_version(tags: list[str], first_version: str) -> str:
    versions = [(k, t) for t in tags if (k := _key(t))]
    if not versions:
        return first_version
    (major, minor, patch), tag = max(versions)
    prefix = _VER.match(tag).group(1)
    return f"{prefix}{major}.{minor}.{patch + 1}"


def find_draft(gh: GitHub, repo: str) -> dict | None:
    for r in gh.list_releases(repo):
        if r.get("draft") and markers.find(r.get("body", ""), "release") is not None:
            return r
    return None


def add_to_draft(gh: GitHub, repo: Repo, merge_sha: str, line: str) -> dict:
    """把一条合并记录加进草稿，没有草稿就新建。返回草稿。"""
    draft = find_draft(gh, repo.full_name)
    if draft is None:
        tag = next_version(gh.list_tags(repo.full_name), repo.first_version)
        body = f"## 变更\n\n- {line}\n\n{markers.make('release', repo=repo.name)}"
        return gh.create_release(repo.full_name, tag_name=tag, name=tag, body=body, draft=True,
                                 target_commitish=merge_sha)
    head, _, tail = draft["body"].partition(markers.make("release", repo=repo.name))
    body = head.rstrip() + f"\n- {line}\n\n" + markers.make("release", repo=repo.name) + tail
    return gh.update_release(repo.full_name, draft["id"], body=body, target_commitish=merge_sha,
                             tag_name=draft["tag_name"])


def ensure_tracking_issue(gh: GitHub, control_repo: str, repo: Repo, draft: dict, bot_login: str) -> None:
    for it in gh.list_open_items(control_repo, "bot:awaiting-approval"):
        if not markers.is_bot(it, bot_login):
            continue
        kv = markers.find(it.get("body", ""), "await")
        if kv and kv.get("action") == "release" and kv.get("repo") == repo.name:
            return
    tag = draft.get("tag_name", "?")
    body = (
        f"`{repo.full_name}` 有已合并的改动待发布，Release 草稿：{draft.get('html_url', '')}\n\n"
        f"草稿 tag：`{tag}`（发布时在草稿的 target commit 上创建）。\n\n"
        "维护者评论 `/approve` 发布，`/reject` 暂不发布（草稿保留，下次合并会再提醒）。\n\n"
        + markers.make("await", action="release", repo=repo.name)
    )
    gh.create_issue(control_repo, f"[发版] {repo.name} {tag} 待发布", body, ["bot:awaiting-approval"])
