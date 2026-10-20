"""Tiny stdlib HTTP helpers shared by every client."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

UA = "hans-torn/2.0"


def get_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_json(url: str, payload: dict, timeout: int = 60) -> dict:
    """POST JSON; on an HTTP error return the parsed error body instead of
    raising (Telegram explains 400s like 'message is not modified')."""
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"ok": False, "description": str(e)}


def post_bytes(url: str, body: bytes, content_type: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": content_type, "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "description": str(e)}
