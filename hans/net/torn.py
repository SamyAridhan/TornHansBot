"""Official Torn API client (read-only). Injectable getter for tests."""

from __future__ import annotations

import urllib.parse
from typing import Callable, Optional

from .http import get_json

TORN_API_BASE = "https://api.torn.com"
USER_SELECTIONS = "bars,cooldowns,travel,profile,battlestats"


class TornApiError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(f"Torn API error {code}: {message}")


def _check(data):
    if isinstance(data, dict) and data.get("error"):
        err = data["error"] if isinstance(data["error"], dict) else {}
        raise TornApiError(err.get("code"), err.get("error") or err.get("message"))
    return data


class Torn:
    def __init__(self, api_key: str, getter: Callable[[str], dict] = get_json):
        self.api_key = api_key
        self.get = getter

    def _url(self, path: str, **params) -> str:
        params["key"] = self.api_key
        return f"{TORN_API_BASE}{path}?{urllib.parse.urlencode(params)}"

    def user(self, selections: str = USER_SELECTIONS) -> dict:
        return _check(self.get(self._url("/user/", selections=selections)))

    def market(self, item_id: str) -> dict:
        """v2 item market listings (v1 'itemmarket' was retired)."""
        return _check(self.get(self._url(f"/v2/market/{item_id}/itemmarket")))

    def faction_crimes(self) -> Optional[dict]:
        """v2 faction OC list; None if the key can't read it."""
        try:
            data = _check(self.get(self._url("/v2/faction/crimes")))
        except Exception:  # noqa: BLE001
            return None
        return data if isinstance(data, dict) else None
