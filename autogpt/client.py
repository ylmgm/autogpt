"""DBeaver-compatible Codex model catalog and streaming Responses client."""

from __future__ import annotations

import json
from collections.abc import Callable

import requests

from .auth import ChatGPTAuth

CODEX_BASE = "https://chatgpt.com/backend-api/codex"
ORIGINATOR = "autogpt"
CLIENT_VERSION = "1.0.0"


def _headers(auth: ChatGPTAuth, *, stream: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {auth.access_token()}",
        "Accept": "text/event-stream" if stream else "application/json",
        "Content-Type": "application/json",
        "User-Agent": f"AutoGPT/{CLIENT_VERSION}",
        "originator": ORIGINATOR,
    }
    if auth.account_id:
        headers["ChatGPT-Account-Id"] = auth.account_id
    return headers


def list_models(auth: ChatGPTAuth) -> list[tuple[str, str]]:
    response = requests.get(
        f"{CODEX_BASE}/models",
        params={"client_version": CLIENT_VERSION},
        headers=_headers(auth),
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"Model list failed (HTTP {response.status_code}): {response.text[:500]}")
    models = response.json().get("models", [])
    visible = [item for item in models if isinstance(item, dict) and item.get("visibility") == "list" and item.get("slug")]
    visible.sort(key=lambda item: item.get("priority", 999999))
    return [(item["slug"], item.get("display_name") or item["slug"]) for item in visible]


def stream_reply(
    auth: ChatGPTAuth,
    model: str,
    messages: list[dict[str, str]],
    on_text: Callable[[str], None],
) -> str:
    response = requests.post(
        f"{CODEX_BASE}/responses",
        headers=_headers(auth, stream=True),
        json={"model": model, "input": messages, "store": False, "stream": True},
        stream=True,
        timeout=(20, 180),
    )
    if not response.ok:
        detail = response.text[:1000]
        raise RuntimeError(f"Chat request failed (HTTP {response.status_code}): {detail}")

    chunks: list[str] = []
    completed = False
    event_type = ""
    try:
        response.encoding = "utf-8"
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            if isinstance(raw_line, bytes):
                raw_line = raw_line.decode("utf-8", errors="replace")
            line = raw_line.strip()
            if line.startswith("event:"):
                event_type = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            kind = event.get("type") or event_type
            if kind == "response.output_text.delta":
                delta = event.get("delta", "")
                chunks.append(delta)
                on_text(delta)
            elif kind in {"response.failed", "error"}:
                error = event.get("response", {}).get("error", {})
                raise RuntimeError(error.get("message") or event.get("message") or "The response failed.")
            elif kind == "response.completed":
                completed = True
        if not completed:
            raise RuntimeError("The response stream ended before completion.")
        return "".join(chunks)
    finally:
        response.close()

