"""Minimal stdlib-only OpenRouter client (transport retries + raw logging via evalkit.net)."""

import os
import time

from . import net

BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")


class OpenRouterError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


def _request(method, path, payload=None, timeout=300, auth=True, retries=4):
    headers = {"X-Title": "web-search-api-comparison"}
    if auth:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise SystemExit("OPENROUTER_API_KEY is not set")
        headers["Authorization"] = f"Bearer {key}"
    try:
        body, _ = net.request_json("openrouter", method, BASE_URL + path, headers, payload, timeout, retries)
        return body
    except net.HTTPStatusError as e:
        raise OpenRouterError(e.status, e.body) from None


def get(path, auth=False, retries=3):
    return _request("GET", path, auth=auth, timeout=60, retries=retries)


def chat(payload):
    """POST /chat/completions. Returns (response_json, latency_seconds incl. retries)."""
    start = time.monotonic()
    body = _request("POST", "/chat/completions", payload)
    return body, time.monotonic() - start


def generation(gen_id, retries=5):
    """GET /generation — authoritative cost + provider for a finished request.

    Stats can lag a second or two behind the completion, so retry on 404.
    """
    for attempt in range(retries):
        try:
            return _request("GET", f"/generation?id={gen_id}", timeout=30)["data"]
        except OpenRouterError as e:
            if e.status != 404 or attempt == retries - 1:
                raise
            time.sleep(1 + attempt)
