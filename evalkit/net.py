"""The one place HTTP happens: JSON requests with transport retries + per-run raw logging.

Retries cover transport problems only: connection errors, timeouts and 408/429/5xx
(honouring Retry-After). Anything else, e.g. a 403 from a site an API was asked to fetch, is
a result and is returned/raised right away.

Note for OpenRouter: a 5xx/timeout after the request reached the model may already have
been billed, so a retry can cost twice. That's accepted: the alternative is a failed run.

Raw logging: while a run is active (`run_log()` context), every attempt of every request
is appended to it with request/response headers (credentials redacted) and bodies.
`run.py` writes it to results/raw/<setup>/<query>.<rep>.json.
"""

import contextlib
import contextvars
import datetime
import http.client
import json
import time
import urllib.error
import urllib.request

RETRY_STATUS = {408, 429, 500, 502, 503, 504}
REDACT = {"authorization", "x-subscription-token", "x-api-key"}
MAX_BACKOFF_S = 60

_current = contextvars.ContextVar("run_log", default=None)


class HTTPStatusError(Exception):
    def __init__(self, status, body, headers):
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status, self.body, self.headers = status, body, headers


@contextlib.contextmanager
def run_log():
    """Collect every HTTP exchange made in this thread/context into a list."""
    exchanges = []
    token = _current.set(exchanges)
    try:
        yield exchanges
    finally:
        _current.reset(token)


def _redact(headers):
    return {k: ("<redacted>" if k.lower() in REDACT else v) for k, v in headers.items()}


def _parse(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


def _log(service, method, url, headers, payload, attempt, started, status, resp_headers, resp_text, error):
    exchanges = _current.get()
    if exchanges is None:
        return
    exchanges.append({
        "service": service, "attempt": attempt,
        "started_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds"),
        "latency_s": round(time.monotonic() - started, 3),
        "request": {"method": method, "url": url, "headers": _redact(headers), "body": payload},
        "response": {"status": status, "headers": resp_headers, "body": _parse(resp_text)},
        "error": error,
    })


def _backoff(attempt, headers):
    retry_after = (headers or {}).get("Retry-After") or (headers or {}).get("retry-after")
    try:
        return min(float(retry_after), MAX_BACKOFF_S)
    except (TypeError, ValueError):
        return min(2 ** attempt, MAX_BACKOFF_S)


def request_json(service, method, url, headers=None, payload=None, timeout=120, retries=4):
    """Returns (parsed body, response headers). Raises HTTPStatusError or the transport error."""
    headers = dict(headers or {})
    data = json.dumps(payload).encode() if payload is not None else None
    if data is not None:
        headers.setdefault("Content-Type", "application/json")
    for attempt in range(retries):
        started = time.monotonic()
        status, resp_headers, text, error, exc = None, {}, None, None, None
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status, resp_headers = resp.status, dict(resp.headers)
                text = resp.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            status, resp_headers = e.code, dict(e.headers or {})
            text = e.read().decode(errors="replace")
            error = f"HTTP {e.code}"
        except (OSError, http.client.HTTPException) as e:  # connection reset, timeout, DNS, ...
            error, exc = f"{type(e).__name__}: {e}", e
        _log(service, method, url, headers, payload, attempt, started, status, resp_headers, text, error)

        if error is None:
            return json.loads(text), resp_headers
        if attempt < retries - 1 and (status is None or status in RETRY_STATUS):
            time.sleep(_backoff(attempt, resp_headers))
            continue
        if status is None:
            raise exc
        raise HTTPStatusError(status, text, resp_headers)
