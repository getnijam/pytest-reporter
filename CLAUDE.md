# pytest-nijam, Claude instructions

pytest plugin for **Nijam**. Implements pytest's hooks to capture a test run and ship it
to the Nijam API. It is the Python sibling of `@nijam/pw-reporter` and reports into the
**same** ingestion endpoints (`/v1/runs`, `…/executions`, `…/source`, `PATCH /v1/runs/:id`).

**This is a public-facing artifact**, installed via `pip`, read on GitHub, pasted into
users' `pytest.ini`. Code quality, **zero runtime dependencies**, and a copy-paste-runnable
README matter more here than anywhere else.

License: **MIT** (separate from the BSL platform, must be maximally adoptable).

> This is an independent repo (`getnijam/pytest-reporter`), not part of a monorepo. It
> shares nothing with the other repos except the API's wire format.

## The one big difference from pw-reporter
**pytest has no traces.** There is no artifact upload path at all (no trace / screenshot /
video). We capture the error log (`longrepr`), the failing line, durations, run stats, and
(opt-in) the test file source, everything the dashboard needs minus the trace viewer.
Projects created as `pytest` in the dashboard hide trace UI accordingly.

## Stack (locked, ask before changing the public option shape)
- Python, `>=3.8`. `from __future__ import annotations` everywhere (3.8-safe typing).
- **Zero runtime dependencies.** `pytest>=7` is the host. HTTP is **stdlib `urllib`** only
  (no `requests`/`httpx`).
- Build: **hatchling**, src-layout. Auto-loads via the `pytest11` entry point in `pyproject.toml`.
- Tooling: `mypy --strict` and `ruff` must stay clean. No automated test suite in v0.1
  (smoke-test by `pip install -e .` into a sample suite).

## Layout
```
src/nijam_pytest/
  plugin.py   # pytest hooks (addoption/configure/sessionstart/logreport/sessionfinish)
  client.py   # NijamClient, HTTP to the API (urllib, soft-fail)
  ci.py       # detect_run_context / relative_file, CI/git metadata (port of pw-reporter ci.ts)
  buffer.py   # ExecutionBuffer, collect during run, flush in chunks at session finish
  models.py   # payload dataclasses (RunContext, TestExecution, FinalizeRunPayload, …)
  log.py      # [nijam]-prefixed warn/info
  cli.py      # `nijam-pytest` console script: fetch-failed (re-run only failures)
```

## Public config surface (design backward from this)
ini options in `pytest.ini` / `[tool.pytest.ini_options]`, each overridable by env (env wins):
`nijam_api_key` (`NIJAM_API_KEY`), `nijam_project_id` (`NIJAM_PROJECT_ID`),
`nijam_api_url` (`NIJAM_API_URL`), `nijam_environment` (`NIJAM_ENVIRONMENT`),
`nijam_upload_source` (bool, default true), `nijam_auto_complete` (bool, default true,
`NIJAM_AUTO_COMPLETE`), `nijam_silent` (bool). Missing key/project → warn with the docs
link + disable; no further work. Don't change these names/shape without asking.

## Lifecycle & behavior
- `pytest_configure` → register one `NijamPlugin`.
- `pytest_sessionstart` → `detect_run_context` + `POST /v1/runs`, store `run_id`; on
  failure log + disable for this run.
- `pytest_runtest_logreport` → accumulate per-attempt state keyed by nodeid (setup → call
  → teardown), emit **one execution per attempt** on the teardown report. Never block on I/O.
- `pytest_sessionfinish` → drain the buffer, optionally upload sources, then
  `PATCH /v1/runs/:id` to finalize with status + stats (unless `auto_complete` is off).
- **Field mapping**: `testId`=nodeid, `title`=last `::` segment, `titlePath`=`::`-split minus
  the file, `file`=git-root-relative (else rootdir-relative) of `report.location[0]`,
  `line`=`report.location[1] + 1` (pytest is 0-based, the API is 1-based), `errorMessage`=
  `longreprtext`, `durationMs`=summed phase durations, `id`=client `uuid4`.
- **Status**: failed > skipped > passed precedence across phases. `xfail` lands as skipped.
  `pytest-rerunfailures` reruns (outcome `rerun`) emit extra attempts with bumped `retry`,
  which is how flaky is derived (a test that passed only after a retry). No reruns ⇒ no flaky.
- **CI detection** (`ci.py`): per-field `CI var → generic GIT_* → git shell-out → empty`.
  GitHub/GitLab/CircleCI/Bitbucket/generic. Leave `branch` None when unknown (dashboard
  renders "No Branch Info"). Same env-var names as pw-reporter's `ci.ts`.
- **HTTP**: Bearer `api_key`, 30s timeout, no retries, 402 → "plan limit reached" warning.
- **Re-run only failures** (`cli.py`, `nijam-pytest` console script): `nijam-pytest fetch-failed` GETs `/v1/projects/:id/failed-tests` (ingest-key authed, identifiers only) and prints the previous run's failed **nodeids** (`pytest $(cat failed.txt)`); via `--export-env "$GITHUB_ENV"` it writes `NIJAM_RUN_GROUP`/`NIJAM_RUN_ATTEMPT`/`NIJAM_RERUN` so the retry's plugin run **clubs under the original run** and is tagged `partialRerun`. `ci.py` honors `NIJAM_RUN_GROUP`/`NIJAM_RUN_ATTEMPT`; `plugin.py` sends `partialRerun` from `NIJAM_RERUN`. The CLI reads config from **env/flags only** (no pytest ini, it runs outside pytest), uses stdlib `argparse`, and is CI-safe: a fetch failure emits nothing + exits 0 (caller's `[ -s failed.txt ]` guard runs the full suite), only bad usage exits non-zero.

## Guard rails, do NOT
- ❌ **Raise from any hook**, wrap every hook body in try/except, `log.warn`, continue.
  The plugin MUST NOT break a user's test run. Ever.
- ❌ Add runtime dependencies (zero-dep goal) · use `requests`/`httpx` (stdlib `urllib` only).
- ❌ Block the test path (`logreport`) on the network, only append to the buffer; flush at finish.
- ❌ Add a trace/artifact upload path, pytest has no traces.
- ❌ Use Python-3.10-only syntax in runtime code without `from __future__ import annotations`.
- ❌ Change the public option names/shape, or the API wire format, without asking.
- ❌ Ternary hell / one-liners that hurt readability, prefer early returns and `if`/`else`.
- ❌ **Em dashes (U+2014) or en dashes (U+2013) anywhere, never generate one.** Not in CLI/log output, error strings, the README, or code/comments. Use a comma, colon, parentheses, or two sentences for prose; a plain hyphen-minus for ranges, IDs, and compound words. The published package must contain zero em/en dashes.

## Build & publish
- `pip install -e '.[dev]'` · `mypy` · `ruff check src` · smoke-test into a sample suite.
- Versions: `0.1.0aN` until platform launch, then `0.1.0`; semver after. Bump the alpha on
  each meaningful change. Publish: `python -m build && twine upload dist/*`.
