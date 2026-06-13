"""HTTP client for the Nijam API — stdlib urllib only (no requests).

Every call is soft-fail: on any error it logs a `[nijam]` warning and returns
None/!ok. The plugin must never break a user's test run, so nothing here raises.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import log
from .models import CreateRunPayload, FinalizeRunPayload, TestExecution

_DEFAULT_API_URL = "https://api.nijam.dev"
_TIMEOUT_S = 30


class NijamClient:
    def __init__(self, api_key: str, api_url: str | None = None) -> None:
        self._api_key = api_key
        # Treat an unset OR blank api_url as "use the default".
        base = (api_url or "").strip() or _DEFAULT_API_URL
        self._base_url = base.rstrip("/")

    def _send(self, method: str, path: str, body: object) -> bytes | None:
        """POST/PATCH a JSON body. Returns response bytes on success, else None."""
        url = f"{self._base_url}{path}"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as res:
                return bytes(res.read())
        except urllib.error.HTTPError as err:
            if err.code == 402:
                log.warn(
                    f"{method} {path} -> 402: plan limit reached; "
                    "upgrade at nijam.dev to keep reporting"
                )
            else:
                log.warn(f"{method} {path} -> {err.code}")
            return None
        except Exception as err:  # URLError, timeout, DNS, TLS, …
            log.warn(f"{method} {url} failed: {err}")
            return None

    def create_run(self, payload: CreateRunPayload) -> dict[str, object] | None:
        """Open a run. Returns {id, url?} or None if the call failed."""
        raw = self._send("POST", "/v1/runs", payload.to_dict())
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except Exception:
            log.warn("POST /v1/runs returned an unparseable body")
            return None
        run_id = data.get("id") or (data.get("run") or {}).get("id")
        if not run_id:
            log.warn("POST /v1/runs returned no run id")
            return None
        return {"id": run_id, "url": data.get("url")}

    def send_executions(self, run_id: str, executions: list[TestExecution]) -> None:
        """Flush a batch of executions. Failed flushes drop the batch (no retry)."""
        body = {"executions": [e.to_dict() for e in executions]}
        self._send("POST", f"/v1/runs/{run_id}/executions", body)

    def upload_source(self, run_id: str, file: str, content: str) -> None:
        """Upload a test file's source for a run (opt-in). Soft-fails like the rest."""
        self._send("POST", f"/v1/runs/{run_id}/source", {"file": file, "content": content})

    def finalize_run(self, run_id: str, payload: FinalizeRunPayload) -> None:
        """Finalize a run with its summary + status."""
        self._send("PATCH", f"/v1/runs/{run_id}", payload.to_dict())
