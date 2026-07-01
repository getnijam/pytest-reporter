"""`nijam-pytest` CLI. The one subcommand, `fetch-failed`, asks the Nijam API which
tests failed in the previous run of this CI run and prints their pytest nodeids, so a
retry runs ONLY the failures::

    nijam-pytest fetch-failed --output failed.txt --export-env "$GITHUB_ENV"
    [ -s failed.txt ] && pytest $(cat failed.txt)

It also writes NIJAM_RUN_GROUP / NIJAM_RUN_ATTEMPT / NIJAM_RERUN (via --export-env) so
the retry's plugin run clubs under the original run in the dashboard.

Unlike the plugin, this is a normal CLI: nodeids go to stdout, diagnostics to stderr.
It never exits non-zero on a fetch/network failure (it just emits nothing, so the
caller's ``[ -s ... ]`` guard runs the full suite), only on bad usage.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import ci
from .client import NijamClient

_DOCS = "https://docs.nijam.dev/guides/rerun-failed-tests/"


def _err(message: str) -> None:
    sys.stderr.write(f"[nijam] {message}\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nijam-pytest",
        description="Nijam pytest helper for re-running only the previously failed tests.",
        epilog=f"Env: NIJAM_API_KEY, NIJAM_PROJECT_ID, NIJAM_API_URL. Docs: {_DOCS}",
    )
    sub = parser.add_subparsers(dest="command")
    fetch = sub.add_parser("fetch-failed", help="Print the previous run's failed test nodeids.")
    fetch.add_argument("-o", "--output", help="Write nodeids to this file (default: stdout).")
    fetch.add_argument(
        "--export-env",
        dest="export_env",
        help='Append NIJAM_RUN_GROUP/ATTEMPT/RERUN as KEY=value lines (use "$GITHUB_ENV").',
    )
    fetch.add_argument("--project", help="Project id (default: $NIJAM_PROJECT_ID).")
    fetch.add_argument(
        "--ci-run-id",
        dest="ci_run_id",
        help="Original CI run id to pull failures from (default: auto-detected).",
    )
    fetch.add_argument("--api-url", dest="api_url", help="API base URL (default: $NIJAM_API_URL).")
    fetch.add_argument("--api-key", dest="api_key", help="Ingest key (default: $NIJAM_API_KEY).")
    return parser


def _fetch_failed(args: argparse.Namespace) -> int:
    api_key = args.api_key or os.environ.get("NIJAM_API_KEY")
    project_id = args.project or os.environ.get("NIJAM_PROJECT_ID")
    if not api_key or not project_id:
        missing = "API key (NIJAM_API_KEY)" if not api_key else "project id (NIJAM_PROJECT_ID)"
        _err(f"missing {missing}")
        return 2

    ctx = ci.detect_run_context()
    ci_run_id = args.ci_run_id or ctx.ciRunId
    if not ci_run_id:
        _err("no CI run id detected; pass --ci-run-id. Nothing to re-run, run the full suite.")
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write("")
        return 0

    client = NijamClient(api_key, args.api_url or os.environ.get("NIJAM_API_URL"))
    result = client.fetch_failed_tests(project_id, ci_run_id)
    raw_tests = (result or {}).get("tests")
    tests = raw_tests if isinstance(raw_tests, list) else []

    # The pytest nodeid IS the testId; feed it straight to `pytest <nodeid>...`.
    nodeids: list[str] = []
    seen: set[str] = set()
    for t in tests:
        node = t.get("testId") if isinstance(t, dict) else None
        if isinstance(node, str) and node not in seen:
            seen.add(node)
            nodeids.append(node)

    body = "\n".join(nodeids) + ("\n" if nodeids else "")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(body)
    else:
        sys.stdout.write(body)

    if args.export_env:
        prior = (result or {}).get("attempt")
        prior_int = prior if isinstance(prior, int) else 0
        native = ctx.ciRunAttempt
        native_int = int(native) if native and native.isdigit() else 0
        next_attempt = max(prior_int + 1, native_int or 1)
        exports = f"NIJAM_RUN_GROUP={ci_run_id}\nNIJAM_RUN_ATTEMPT={next_attempt}\nNIJAM_RERUN=1\n"
        with open(args.export_env, "a", encoding="utf-8") as f:
            f.write(exports)

    _err(
        f"{len(nodeids)} failed test(s) from the previous run; feed them to pytest"
        if nodeids
        else "no failed tests from the previous run; nothing to re-run"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "fetch-failed":
        return _fetch_failed(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
