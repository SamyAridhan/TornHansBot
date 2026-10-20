"""Inline button layouts. Callback data is short and namespaced:

  nav:<screen>          open a screen (home, next, travel, oc, stats, set, set:<page>)
  set:<key>:<page>      flip/cycle one setting, then redraw that settings page
  stk:<code>            open one country's stock + depart detail
  w:<code>              toggle that country's 'leave now' alert
  oc:in:<hours> | oc:auto | oc:clear
  run:digest | run:chart | run:buzz
  ack                   stop the landing buzz
"""

from __future__ import annotations

from .. import settings as S
from ..core import travel


def _kb(rows):
    return {"inline_keyboard": rows}


def _b(text, data):
    return {"text": text, "callback_data": data}


HOME = _kb([
    [_b("🔄 Refresh", "nav:home"), _b("⏱ Next full", "nav:next")],
    [_b("✈️ Travel", "nav:travel"), _b("⚔️ OC", "nav:oc")],
    [_b("💪 Stats", "nav:stats"), _b("⚙️ Settings", "nav:set")],
])

ACK = _kb([[_b("✅ Got it — stop buzzing", "ack")]])


def back(to: str = "home", label: str = "⬅ Back") -> dict:
    return _kb([[_b(label, f"nav:{to}")]])


def stats() -> dict:
    return _kb([
        [_b("📨 Digest now", "run:digest"), _b("📈 Chart now", "run:chart")],
        [_b("⬅ Home", "nav:home")],
    ])


def oc() -> dict:
    return _kb([
        [_b("in 6h", "oc:in:6"), _b("in 12h", "oc:in:12"),
         _b("in 18h", "oc:in:18"), _b("in 24h", "oc:in:24")],
        [_b("🔄 Auto-read", "oc:auto"), _b("🗑 Clear", "oc:clear")],
        [_b("⬅ Home", "nav:home")],
    ])


def travel_menu(routes: dict) -> dict:
    """Grid of countries (🟢 = 'leave now' alert on); tap for detail."""
    btns = []
    for code in travel.COUNTRIES:
        on = (routes.get(code) or {}).get("enabled")
        label = travel.country_name(code).replace("Cayman Islands", "Cayman") \
            .replace("United Kingdom", "UK").replace("South Africa", "S. Africa")
        btns.append(_b(("🟢 " if on else "") + label, f"stk:{code}"))
    rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    rows.append([_b("⚙️ Travel settings", "nav:set:travel"), _b("⬅ Home", "nav:home")])
    return _kb(rows)


def route(code: str, enabled: bool) -> dict:
    return _kb([
        [_b(("🔔 'Leave now' alert: ON" if enabled else "🔕 'Leave now' alert: OFF"),
            f"w:{code}")],
        [_b("🔄 Refresh", f"stk:{code}"), _b("⬅ Travel", "nav:travel")],
    ])


def settings_home() -> dict:
    return _kb([
        [_b(S.PAGES["alerts"][0], "nav:set:alerts"), _b(S.PAGES["travel"][0], "nav:set:travel")],
        [_b(S.PAGES["daily"][0], "nav:set:daily"), _b(S.PAGES["quiet"][0], "nav:set:quiet")],
        [_b("⬅ Home", "nav:home")],
    ])


def settings_page(page: str, settings: dict) -> dict:
    rows, pair = [], []
    for key in S.PAGES[page][1]:
        path, label, kind = S.SPEC[key]
        val = S.show_value(key, S.get(settings, path))
        btn = _b(f"{val} {label}" if kind == "toggle" else f"{label}: {val} ↻",
                 f"set:{key}:{page}")
        if kind == "toggle" and page == "alerts":
            pair.append(btn)          # alerts page: two toggles per row
            if len(pair) == 2:
                rows.append(pair)
                pair = []
            continue
        if pair:
            rows.append(pair)
            pair = []
        rows.append([btn])
    if pair:
        rows.append(pair)
    extra = []
    if page == "travel":
        extra.append(_b("🔔 Test buzz", "run:buzz"))
    extra.append(_b("⬅ Settings", "nav:set"))
    rows.append(extra)
    return _kb(rows)
