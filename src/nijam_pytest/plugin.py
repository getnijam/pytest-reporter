"""Nijam pytest plugin. Captures a test run and ships it to the Nijam API.

Golden rule (ported from pw-reporter): NEVER raise from a hook. Every hook body is
wrapped in try/except; on any failure we log a `[nijam]` warning and continue (or
no-op the run). The plugin must never break a user's test session.

pytest has no traces, so, unlike the Playwright reporter, there is no artifact
upload path. We still capture the error log (`longrepr`), the failing line, duration,
and (opt-in) the test file's source, which is everything the dashboard needs.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from . import ci, log
from .buffer import ExecutionBuffer
from .client import NijamClient
from .models import CreateRunPayload, FinalizeRunPayload, RunStats, TestExecution

SETUP_DOCS = "https://docs.nijam.dev/reporter/pytest/"
_SOURCE_MAX_BYTES = 256 * 1024
_FALSEY = {"false", "0", "no", "off"}


def _now_iso() -> str:
    """ISO-8601 in UTC with a trailing Z (the form Zod's .datetime() accepts)."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class _PendingTest:
    """Accumulates one attempt's reports (setup → call → teardown) into a row."""

    test_id: str
    title: str
    title_path: list[str]
    file: str
    started_at: str
    line: int | None = None
    duration_ms: int = 0
    error_message: str | None = None
    failed: bool = False
    skipped: bool = False


# ---- pytest configuration hooks -------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register ini options. Each is also overridable by an env var (handy for CI
    secrets): NIJAM_API_KEY / NIJAM_PROJECT_ID / NIJAM_API_URL / NIJAM_ENVIRONMENT /
    NIJAM_AUTO_COMPLETE."""
    parser.addini("nijam_api_key", "Nijam ingest API key (or env NIJAM_API_KEY).", default="")
    parser.addini("nijam_project_id", "Nijam project UUID (or env NIJAM_PROJECT_ID).", default="")
    parser.addini("nijam_api_url", "Nijam API base URL (default api.nijam.dev).", default="")
    parser.addini("nijam_environment", "Free-form environment tag (e.g. staging).", default="")
    parser.addini(
        "nijam_upload_source",
        "Upload each test file's source for the dashboard (default true).",
        type="bool",
        default=True,
    )
    parser.addini(
        "nijam_auto_complete",
        "Finalize the run when this process ends (default true).",
        type="bool",
        default=True,
    )
    parser.addini("nijam_silent", "Suppress [nijam] log lines.", type="bool", default=False)


def pytest_configure(config: pytest.Config) -> None:
    config.pluginmanager.register(NijamPlugin(config), "nijam-plugin")


def _resolve_str(config: pytest.Config, ini: str, env: str) -> str | None:
    """Env var wins (CI secrets), then the ini value; blank → None."""
    from_env = os.environ.get(env)
    if from_env and from_env.strip():
        return from_env.strip()
    value = config.getini(ini)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _resolve_auto_complete(config: pytest.Config) -> bool:
    env = os.environ.get("NIJAM_AUTO_COMPLETE")
    if env is not None and env.strip().lower() in _FALSEY:
        return False
    return bool(config.getini("nijam_auto_complete"))


def _run_status(exitstatus: int) -> str:
    """Map pytest's ExitCode to the run status enum (passed|failed|interrupted)."""
    if exitstatus == int(pytest.ExitCode.INTERRUPTED):
        return "interrupted"
    if exitstatus in (int(pytest.ExitCode.OK), int(pytest.ExitCode.NO_TESTS_COLLECTED)):
        return "passed"
    return "failed"


def _longrepr_text(report: pytest.TestReport) -> str | None:
    """The failure's error log, if any."""
    text = getattr(report, "longreprtext", "") or ""
    if text:
        return text
    longrepr = report.longrepr
    return str(longrepr) if longrepr else None


# ---- the plugin -----------------------------------------------------------------


class NijamPlugin:
    def __init__(self, config: pytest.Config) -> None:
        self._enabled = False
        self._run_id: str | None = None
        self._run_url: str | None = None
        self._started_at = _now_iso()
        self._root_dir = str(config.rootpath)
        self._git_root = ci.detect_git_root()
        self._upload_source = bool(config.getini("nijam_upload_source"))
        self._auto_complete = _resolve_auto_complete(config)
        self._environment = _resolve_str(config, "nijam_environment", "NIJAM_ENVIRONMENT")

        # rel path → abs path, for opt-in source upload at session finish.
        self._source_files: dict[str, str] = {}
        # In-flight per-attempt accumulators, keyed by nodeid.
        self._pending: dict[str, _PendingTest] = {}
        # Attempt counters / final outcomes per nodeid (drive retry + run stats).
        self._attempts: dict[str, int] = {}
        self._final: dict[str, str] = {}
        self._max_retry: dict[str, int] = {}

        log.set_silent(bool(config.getini("nijam_silent")))
        api_key = _resolve_str(config, "nijam_api_key", "NIJAM_API_KEY")
        self._project_id = _resolve_str(config, "nijam_project_id", "NIJAM_PROJECT_ID")
        api_url = _resolve_str(config, "nijam_api_url", "NIJAM_API_URL")

        if not api_key or not self._project_id:
            missing = "nijam_api_key" if not api_key else "nijam_project_id"
            log.warn(f"missing {missing}, reporter disabled. See {SETUP_DOCS}")
            return

        self._client = NijamClient(api_key, api_url)
        self._buffer = ExecutionBuffer(self._flush_batch)
        self._enabled = True

    # --- lifecycle ---

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        if not self._enabled:
            return
        try:
            context = ci.detect_run_context()
            self._started_at = _now_iso()
            created = self._client.create_run(
                CreateRunPayload(
                    projectId=self._project_id or "",
                    startedAt=self._started_at,
                    context=context,
                    environment=self._environment,
                )
            )
            if not created:
                self._enabled = False
                log.warn("could not create run; reporting disabled for this run")
                return
            self._run_id = str(created["id"])
            url = created.get("url")
            self._run_url = str(url) if url else None
            if self._run_url:
                log.info(f"run started, view it at {self._run_url}")
            else:
                log.info(f"run started ({self._run_id})")
        except Exception as err:
            self._enabled = False
            log.warn(f"sessionstart failed: {err}")

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if not self._enabled or not self._run_id:
            return
        try:
            self._record(report)
        except Exception as err:
            log.warn(f"logreport failed: {err}")

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if not self._enabled or not self._run_id:
            return
        try:
            self._buffer.drain()
            if self._upload_source:
                self._upload_sources()

            if not self._auto_complete:
                log.info("this job done, complete the run via your post-matrix step")
                if self._run_url:
                    log.info(f"view the run at {self._run_url}")
                return

            stats = self._compute_stats()
            self._client.finalize_run(
                self._run_id,
                FinalizeRunPayload(
                    status=_run_status(exitstatus),
                    finishedAt=_now_iso(),
                    stats=stats,
                ),
            )
            suffix = f", view it at {self._run_url}" if self._run_url else ""
            log.info(f"run finalized ({stats.passed}/{stats.total} passed){suffix}")
        except Exception as err:
            log.warn(f"sessionfinish failed: {err}")

    # --- internals ---

    def _flush_batch(self, batch: list[TestExecution]) -> None:
        if not self._run_id:
            return
        self._client.send_executions(self._run_id, batch)

    def _record(self, report: pytest.TestReport) -> None:
        nodeid = report.nodeid
        pending = self._pending.get(nodeid)
        if pending is None:
            pending = self._new_pending(nodeid, report)
            self._pending[nodeid] = pending

        pending.duration_ms += int(round(report.duration * 1000))

        # pytest-rerunfailures flags a retried attempt with outcome 'rerun' (a value
        # outside pytest's own outcome Literal, so read it dynamically).
        is_rerun = getattr(report, "outcome", None) == "rerun"
        if is_rerun or report.failed:
            pending.failed = True
            if not pending.error_message:
                pending.error_message = _longrepr_text(report)
        elif report.skipped and report.when != "teardown":
            # A skip during setup/call marks the test skipped (xfail lands here too).
            pending.skipped = True

        if report.when == "teardown":
            self._emit(nodeid, pending)
            del self._pending[nodeid]

    def _new_pending(self, nodeid: str, report: pytest.TestReport) -> _PendingTest:
        rel_path, lineno, _domain = report.location
        abs_path = os.path.join(self._root_dir, rel_path) if rel_path else nodeid.split("::", 1)[0]
        file = ci.relative_file(abs_path, self._root_dir, self._git_root)
        if self._upload_source and rel_path and file not in self._source_files:
            self._source_files[file] = abs_path

        parts = nodeid.split("::")
        # Drop the leading file segment from the path, the file is its own field.
        title_path = parts[1:] if len(parts) > 1 else parts
        line = lineno + 1 if isinstance(lineno, int) else None
        return _PendingTest(
            test_id=nodeid,
            title=parts[-1] if parts else nodeid,
            title_path=title_path,
            file=file,
            line=line,
            started_at=_now_iso(),
        )

    def _emit(self, nodeid: str, p: _PendingTest) -> None:
        if p.failed:
            status = "failed"
        elif p.skipped:
            status = "skipped"
        else:
            status = "passed"

        retry = self._attempts.get(nodeid, 0)
        self._attempts[nodeid] = retry + 1
        self._final[nodeid] = status
        if retry > self._max_retry.get(nodeid, 0):
            self._max_retry[nodeid] = retry

        self._buffer.add(
            TestExecution(
                id=str(uuid.uuid4()),
                testId=nodeid,
                title=p.title,
                titlePath=p.title_path,
                file=p.file,
                status=status,
                durationMs=p.duration_ms,
                retry=retry,
                startedAt=p.started_at,
                errorMessage=p.error_message if status == "failed" else None,
                line=p.line,
            )
        )

    def _compute_stats(self) -> RunStats:
        stats = RunStats()
        for nodeid, status in self._final.items():
            stats.total += 1
            if status == "failed":
                stats.failed += 1
            elif status == "skipped":
                stats.skipped += 1
            else:
                stats.passed += 1
                # A test that passed only after a retry is flaky (needs rerunfailures).
                if self._max_retry.get(nodeid, 0) > 0:
                    stats.flaky += 1
        return stats

    def _upload_sources(self) -> None:
        if not self._run_id:
            return
        for rel, abs_path in self._source_files.items():
            try:
                with open(abs_path, encoding="utf-8") as f:
                    content = f.read()
            except Exception as err:
                log.warn(f"source read failed for {rel}: {err}")
                continue
            if len(content.encode("utf-8")) > _SOURCE_MAX_BYTES:
                continue  # skip oversized
            self._client.upload_source(self._run_id, rel, content)
