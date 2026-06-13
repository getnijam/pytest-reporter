# pytest-nijam

pytest plugin for [Nijam](https://nijam.dev) — captures your test runs and ships them
to the Nijam dashboard so you can track what failed, why, and where (error log +
failing line), across CI runs and over time.

> pytest has no traces, so — unlike the Playwright reporter — runs won't include a
> trace viewer. Everything else (failures, error output, the failing line, durations,
> and your test source) is captured.

## Install

```bash
pip install pytest-nijam
```

The plugin auto-activates once installed (via pytest's `pytest11` entry point).

## Configure

Add your project ID to `pytest.ini` (or `pyproject.toml`), and provide the ingest API
key via an environment variable (it's a secret — keep it out of source control):

```ini
# pytest.ini
[pytest]
nijam_project_id = 00000000-0000-0000-0000-000000000000
```

```toml
# pyproject.toml
[tool.pytest.ini_options]
nijam_project_id = "00000000-0000-0000-0000-000000000000"
```

```bash
export NIJAM_API_KEY="nij_sk_…"   # from the Nijam dashboard → Secret keys
pytest
```

Both the project ID and the API key can come from either the ini file or an
environment variable; **the environment variable wins** when both are set.

## Options

| ini option            | env var               | default                  | what it does                                                        |
| --------------------- | --------------------- | ------------------------ | ------------------------------------------------------------------- |
| `nijam_api_key`       | `NIJAM_API_KEY`       | —                        | Ingest API key (required).                                          |
| `nijam_project_id`    | `NIJAM_PROJECT_ID`    | —                        | Project UUID (required).                                            |
| `nijam_api_url`       | `NIJAM_API_URL`       | `https://api.nijam.dev`  | API base URL.                                                       |
| `nijam_environment`   | `NIJAM_ENVIRONMENT`   | —                        | Free-form environment tag (e.g. `staging`).                         |
| `nijam_upload_source` | —                     | `true`                   | Upload each test file's source so the dashboard can show it.        |
| `nijam_auto_complete` | `NIJAM_AUTO_COMPLETE` | `true`                   | Finalize the run when this process ends. Set `false` for fan-out.   |
| `nijam_silent`        | —                     | `false`                  | Suppress `[nijam]` log lines.                                       |

If `nijam_api_key` or `nijam_project_id` is missing, the plugin disables itself with a
single warning — your tests run exactly as before.

## CI metadata

Run context (commit, branch, PR number, CI provider/run URL, commit author) is detected
automatically from GitHub Actions, GitLab CI, CircleCI, Bitbucket Pipelines, or generic
`GIT_*` env vars, falling back to `git` itself. No configuration needed.

## Fan-out across CI jobs

If you split your suite across several CI jobs (e.g. one job per test path) that all feed
one Nijam run, set `nijam_auto_complete = false` (or `NIJAM_AUTO_COMPLETE=false`) on each
job so none of them finalizes early, then complete the run once from a post-matrix step.
See the [docs](https://docs.nijam.dev/reporter/pytest/).

## Guarantees

This plugin **never breaks your test run.** Every hook is fail-soft: a network error, a
bad key, or an unreachable API produces a `[nijam]` warning and nothing more — your tests
still pass or fail on their own merits.

## License

MIT
