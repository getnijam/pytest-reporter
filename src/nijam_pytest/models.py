"""Payload shapes mirroring the Nijam API's Zod schemas (see api/src/routes/runs).

The pytest path uses the exact same ingestion endpoints as the Playwright reporter;
the only difference is pytest never produces traces, so there is no artifact payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _compact(d: dict[str, object]) -> dict[str, object]:
    """Drop keys whose value is None, the API treats absent and null alike, and a
    leaner body keeps the wire format close to the Playwright reporter's."""
    return {k: v for k, v in d.items() if v is not None}


@dataclass
class RunContext:
    """CI / git metadata detected from the environment (see ci.py)."""

    commitSha: str | None = None
    branch: str | None = None
    prNumber: str | None = None
    ciProvider: str | None = None
    # CI run attempt (e.g. GITHUB_RUN_ATTEMPT), re-runs get a fresh Nijam run.
    ciRunAttempt: str | None = None
    ciRunId: str | None = None
    ciRunUrl: str | None = None
    repository: str | None = None
    authorEmail: str | None = None
    authorName: str | None = None

    def to_dict(self) -> dict[str, object]:
        return _compact(
            {
                "commitSha": self.commitSha,
                "branch": self.branch,
                "prNumber": self.prNumber,
                "ciProvider": self.ciProvider,
                "ciRunAttempt": self.ciRunAttempt,
                "ciRunId": self.ciRunId,
                "ciRunUrl": self.ciRunUrl,
                "repository": self.repository,
                "authorEmail": self.authorEmail,
                "authorName": self.authorName,
            }
        )


@dataclass
class CreateRunPayload:
    """Body for POST /v1/runs."""

    projectId: str
    startedAt: str
    context: RunContext = field(default_factory=RunContext)
    environment: str | None = None
    # True when this run re-ran only the previous attempt's failed tests (NIJAM_RERUN).
    partialRerun: bool = False

    def to_dict(self) -> dict[str, object]:
        body = self.context.to_dict()
        body["projectId"] = self.projectId
        body["startedAt"] = self.startedAt
        if self.environment is not None:
            body["environment"] = self.environment
        if self.partialRerun:
            body["partialRerun"] = True
        return body


@dataclass
class TestExecution:
    """One test attempt, buffered and flushed in batches to POST …/executions.

    `status` is one of passed | failed | skipped (the API also accepts timedOut /
    interrupted, which pytest doesn't surface per-test). There is no `line` field for
    runs that predate line capture, pytest provides it, so we always send it.
    """

    id: str  # client-generated uuid (PK), mirrors the Playwright reporter
    testId: str
    title: str
    titlePath: list[str]
    file: str
    status: str
    durationMs: int
    retry: int
    startedAt: str
    errorMessage: str | None = None
    line: int | None = None

    def to_dict(self) -> dict[str, object]:
        return _compact(
            {
                "id": self.id,
                "testId": self.testId,
                "title": self.title,
                "titlePath": self.titlePath,
                "file": self.file,
                "status": self.status,
                "durationMs": self.durationMs,
                "retry": self.retry,
                "startedAt": self.startedAt,
                "errorMessage": self.errorMessage,
                "line": self.line,
            }
        )


@dataclass
class RunStats:
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    flaky: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "flaky": self.flaky,
        }


@dataclass
class FinalizeRunPayload:
    """Body for PATCH /v1/runs/:id. status is passed | failed | interrupted."""

    status: str
    finishedAt: str
    stats: RunStats

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "finishedAt": self.finishedAt,
            "stats": self.stats.to_dict(),
        }
