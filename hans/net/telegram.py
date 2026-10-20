"""Telegram Bot API client. Every method is best-effort: failures come back as
{"ok": False, ...} rather than exceptions, except get_updates (the loop needs
to know a long-poll failed so it can back off)."""

from __future__ import annotations

import io
import json
import urllib.parse
import uuid
from typing import Callable, Optional

from .http import get_json, post_bytes, post_json

API = "https://api.telegram.org"


class Telegram:
    def __init__(self, token: str, chat_id: str,
                 post: Callable[[str, dict], dict] = post_json,
                 getter: Callable[..., dict] = get_json):
        self.token = token
        self.chat_id = str(chat_id)
        self._post = post
        self._get = getter

    def call(self, method: str, payload: dict) -> dict:
        try:
            return self._post(f"{API}/bot{self.token}/{method}", payload) or {}
        except Exception as e:  # noqa: BLE001 — network blip
            return {"ok": False, "description": str(e)}

    # ---- messages -------------------------------------------------------
    def send(self, text: str, markup: Optional[dict] = None,
             silent: bool = False) -> Optional[int]:
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        if markup:
            payload["reply_markup"] = markup
        if silent:
            payload["disable_notification"] = True
        r = self.call("sendMessage", payload)
        return (r.get("result") or {}).get("message_id")

    def edit(self, message_id: int, text: str, markup: Optional[dict] = None) -> dict:
        """Edit in place. 'not modified' counts as success."""
        payload = {"chat_id": self.chat_id, "message_id": message_id, "text": text,
                   "parse_mode": "HTML", "disable_web_page_preview": True}
        if markup:
            payload["reply_markup"] = markup
        r = self.call("editMessageText", payload)
        if not r.get("ok") and "not modified" in (r.get("description") or "").lower():
            return {"ok": True, "not_modified": True}
        return r

    def pin(self, message_id: int) -> None:
        self.call("pinChatMessage", {"chat_id": self.chat_id,
                                     "message_id": message_id,
                                     "disable_notification": True})

    def answer(self, callback_id: str, text: str = "") -> None:
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        self.call("answerCallbackQuery", payload)

    def photo_url(self, url: str, caption: str, markup: Optional[dict] = None) -> bool:
        payload = {"chat_id": self.chat_id, "photo": url, "caption": caption,
                   "parse_mode": "HTML"}
        if markup:
            payload["reply_markup"] = markup
        return bool(self.call("sendPhoto", payload).get("ok"))

    def photo_bytes(self, png: bytes, caption: str) -> bool:
        body, boundary = _multipart(
            {"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML"},
            "photo", "growth.png", png)
        r = post_bytes(f"{API}/bot{self.token}/sendPhoto", body,
                       f"multipart/form-data; boundary={boundary}")
        return bool(r.get("ok"))

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        self.call("setMyCommands", {"commands": [
            {"command": c, "description": d} for c, d in commands]})

    # ---- updates --------------------------------------------------------
    def get_updates(self, offset: Optional[int], timeout: int) -> list[dict]:
        params = {"timeout": max(0, int(timeout)),
                  "allowed_updates": json.dumps(["message", "callback_query"])}
        if offset is not None:
            params["offset"] = offset
        url = f"{API}/bot{self.token}/getUpdates?{urllib.parse.urlencode(params)}"
        data = self._get(url, timeout=int(timeout) + 15)
        return data.get("result", []) if data.get("ok") else []


def _multipart(fields: dict, file_field: str, filename: str, file_bytes: bytes):
    boundary = f"----hans{uuid.uuid4().hex}"
    nl = b"\r\n"
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(b"--" + boundary.encode() + nl)
        out.write(f'Content-Disposition: form-data; name="{k}"'.encode() + nl + nl)
        out.write(str(v).encode() + nl)
    out.write(b"--" + boundary.encode() + nl)
    out.write(f'Content-Disposition: form-data; name="{file_field}"; '
              f'filename="{filename}"'.encode() + nl)
    out.write(b"Content-Type: image/png" + nl + nl)
    out.write(file_bytes + nl)
    out.write(b"--" + boundary.encode() + b"--" + nl)
    return out.getvalue(), boundary
