"""CI / git metadata detection — a Python port of pw-reporter's ci.ts.

Per-field resolution order: CI-specific var > generic GIT_* var > git shell-out >
empty. Branch stays None when unknown (the dashboard renders "No Branch Info").
Every git shell-out swallows errors (shallow clones, no git installed, etc.).
"""

from __future__ import annotations

import json
import os
import re
import subprocess

from .models import RunContext

_env = os.environ
_GIT_TIMEOUT = 5


def _first_of(*values: str | None) -> str | None:
    """First non-empty (stripped) value wins."""
    for v in values:
        if v and v.strip():
            return v.strip()
    return None


def _git(*args: str) -> str | None:
    """Run a git command, returning trimmed stdout or None on any failure."""
    try:
        out = subprocess.run(
            ["git", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=_GIT_TIMEOUT,
            check=True,
            text=True,
        ).stdout.strip()
        return out or None
    except Exception:
        return None


def detect_git_root() -> str | None:
    """Absolute path of the git repo root — a portable base for spec paths."""
    return _git("rev-parse", "--show-toplevel")


def _git_head() -> str | None:
    return _git("rev-parse", "HEAD")


def _git_author() -> tuple[str | None, str | None]:
    """HEAD commit author (email, name) from git."""
    out = _git("log", "-1", "--format=%ae%n%an")
    if not out:
        return (None, None)
    lines = out.split("\n")
    email = lines[0].strip() if len(lines) > 0 and lines[0].strip() else None
    name = lines[1].strip() if len(lines) > 1 and lines[1].strip() else None
    return (email, name)


def _git_config_email() -> str | None:
    return _git("config", "user.email")


def _parse_author(raw: str | None) -> tuple[str | None, str | None]:
    """Parse a "Display Name <email@host>" string into (name, email)."""
    if not raw:
        return (None, None)
    m = re.match(r"^\s*(.*?)\s*<([^>]+)>\s*$", raw)
    if m:
        name = m.group(1).strip() or None
        email = m.group(2).strip() or None
        return (name, email)
    if "@" in raw:
        return (None, raw.strip())
    return (raw.strip(), None)


def _github_pr_head_sha() -> str | None:
    """On a GitHub `pull_request` run, GITHUB_SHA is the synthetic merge commit;
    PR checks must target the PR *head* SHA. Read it from the event payload."""
    event_name = _env.get("GITHUB_EVENT_NAME")
    path = _env.get("GITHUB_EVENT_PATH")
    if not path or event_name not in ("pull_request", "pull_request_target"):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            event = json.load(f)
        sha = event.get("pull_request", {}).get("head", {}).get("sha")
        return sha if isinstance(sha, str) else None
    except Exception:
        return None


def _github_run_url() -> str | None:
    run_id = _env.get("GITHUB_RUN_ID")
    repo = _env.get("GITHUB_REPOSITORY")
    if not run_id or not repo:
        return None
    server = _env.get("GITHUB_SERVER_URL") or "https://github.com"
    return f"{server}/{repo}/actions/runs/{run_id}"


def _bitbucket_run_url() -> str | None:
    origin = _env.get("BITBUCKET_GIT_HTTP_ORIGIN")
    num = _env.get("BITBUCKET_BUILD_NUMBER")
    if not origin or not num:
        return None
    return f"{origin.rstrip('/')}/pipelines/results/{num}"


def _detect_ci_provider() -> str | None:
    """First matching CI provider, in priority order; None when not on known CI."""
    if _env.get("GITHUB_ACTIONS"):
        return "github"
    if _env.get("GITLAB_CI"):
        return "gitlab"
    if _env.get("CIRCLECI"):
        return "circleci"
    if _env.get("BITBUCKET_BUILD_NUMBER") or _env.get("BITBUCKET_PIPELINE_UUID"):
        return "bitbucket"
    if _env.get("CI"):
        return "generic"
    return None


def _pr_number_from_github_ref() -> str | None:
    """GitHub exposes no PR-number var — derive it from refs/pull/<n>/merge."""
    ref = _env.get("GITHUB_REF")
    if not ref:
        return None
    m = re.match(r"^refs/pull/(\d+)/", ref)
    return m.group(1) if m else None


def _circle_pr_number() -> str | None:
    pr = _env.get("CIRCLE_PULL_REQUEST")
    return pr.split("/")[-1] if pr else None


def detect_run_context() -> RunContext:
    """Detect commit / branch / PR / CI run id+url / git author from the environment."""
    provider = _detect_ci_provider()

    commit_sha = _first_of(
        _github_pr_head_sha(),  # PR head, not the merge commit, so PR checks land right
        _env.get("GITHUB_SHA"),
        _env.get("CI_COMMIT_SHA"),
        _env.get("CIRCLE_SHA1"),
        _env.get("BITBUCKET_COMMIT"),
        _env.get("COMMIT_SHA"),
        _env.get("GIT_COMMIT"),
        _git_head(),
    )

    branch = _first_of(
        _env.get("GITHUB_HEAD_REF"),  # PR source branch on GitHub
        _env.get("GITHUB_REF_NAME"),
        _env.get("CI_COMMIT_REF_NAME"),
        _env.get("CIRCLE_BRANCH"),
        _env.get("BITBUCKET_BRANCH"),  # absent on tag builds → stays None
        _env.get("BRANCH"),
        _env.get("GIT_BRANCH"),
    )

    pr_number = _first_of(
        _pr_number_from_github_ref(),
        _env.get("CI_MERGE_REQUEST_IID"),
        _circle_pr_number(),
        _env.get("BITBUCKET_PR_ID"),
    )

    ci_run_id = _first_of(
        _env.get("GITHUB_RUN_ID"),
        _env.get("CI_PIPELINE_ID"),  # GitLab
        _env.get("CIRCLE_BUILD_NUM"),
        _env.get("BITBUCKET_BUILD_NUMBER"),
        _env.get("CI_RUN_ID"),
    )

    # A re-run keeps the same run id but bumps the attempt; include it so a re-run is
    # a fresh run, not merged into the prior one.
    ci_run_attempt = _first_of(_env.get("GITHUB_RUN_ATTEMPT"))

    ci_run_url = _first_of(
        _github_run_url(),
        _env.get("CI_PIPELINE_URL"),
        _env.get("CIRCLE_BUILD_URL"),
        _bitbucket_run_url(),
        _env.get("CI_URL"),
    )

    repository = _first_of(
        _env.get("GITHUB_REPOSITORY"),
        _env.get("CI_PROJECT_PATH"),
        _env.get("BITBUCKET_REPO_FULL_NAME"),
    )

    # Author — single git shell-out, reused for email + name.
    git_email, git_name = _git_author()
    commit_author_name, commit_author_email = _parse_author(_env.get("CI_COMMIT_AUTHOR"))
    bitbucket_author_name, _ = _parse_author(_env.get("BITBUCKET_COMMIT_AUTHOR"))

    author_email = _first_of(
        _env.get("GITLAB_USER_EMAIL"),
        commit_author_email,
        git_email,  # GitHub/CircleCI/Bitbucket expose no author-email var
        _git_config_email(),
    )

    author_name = _first_of(
        _env.get("GITLAB_USER_NAME"),
        bitbucket_author_name,
        commit_author_name,
        git_name,
    )

    return RunContext(
        commitSha=commit_sha,
        branch=branch,
        prNumber=pr_number,
        ciProvider=provider,
        ciRunAttempt=ci_run_attempt,
        ciRunId=ci_run_id,
        ciRunUrl=ci_run_url,
        repository=repository,
        authorEmail=author_email,
        authorName=author_name,
    )


def relative_file(file: str, root_dir: str | None, git_root: str | None) -> str:
    """Spec path relative to the git repo root when available, else pytest's rootdir,
    normalized to forward slashes. The repo-root form is what the dashboard needs for
    a working "View source" link. Never returns an absolute machine path."""
    bases: list[str] = [b for b in (git_root, root_dir) if b]
    for base in bases:
        try:
            rel = os.path.relpath(file, base)
        except ValueError:
            continue
        if rel and not rel.startswith("..") and not os.path.isabs(rel):
            return rel.replace(os.sep, "/")
    return re.split(r"[\\/]", file)[-1] or file
