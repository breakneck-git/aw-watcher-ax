"""Per-app context extraction strategies.

A strategy takes an AXUIElement for the running app process and returns a
short string (chat name, conversation title, file name) or None if nothing
useful could be extracted.

Built-in extractors in BUILTIN embed app-specific heuristics (how Telegram
structures its AX tree). Generic fallbacks - `_extract_heading` and
`_extract_window_title` - work for most apps and are selectable via the
`strategy` field in config.
"""

from collections.abc import Callable
from typing import Any

from .ax_utils import ax_get, ax_walk, create_app_element
from .config import AppConfig

_MAX_LEN = 200


def _clean(value: Any, app_name: str) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s == app_name:
        return None
    return s[:_MAX_LEN]


def _extract_window_title(app_el: Any, app_name: str) -> str | None:
    win = ax_get(app_el, "AXFocusedWindow")
    if win is None:
        return None
    return _clean(ax_get(win, "AXTitle"), app_name)


def _extract_heading(app_el: Any, app_name: str) -> str | None:
    win = ax_get(app_el, "AXFocusedWindow")
    if win is None:
        return None
    for el in ax_walk(win, role="AXHeading", max_depth=15):
        text = ax_get(el, "AXTitle") or ax_get(el, "AXValue")
        cleaned = _clean(text, app_name)
        if cleaned:
            return cleaned
    return None


def _extract_telegram(app_el: Any, app_name: str) -> str | None:
    win = ax_get(app_el, "AXFocusedWindow")
    if win is None:
        return None
    for row in ax_walk(win, role="AXRow", max_depth=20):
        if not ax_get(row, "AXSelected"):
            continue
        text = ax_get(row, "AXDescription") or ax_get(row, "AXTitle")
        cleaned = _clean(text, app_name)
        if cleaned:
            return cleaned
    return None


BUILTIN: dict[str, Callable[[Any, str], str | None]] = {
    "ru.keepcoder.Telegram": _extract_telegram,
}


def extract_context(app_cfg: AppConfig, pid: int) -> str | None:
    app_el = create_app_element(pid)
    if app_el is None:
        return None

    strategy = app_cfg.strategy
    if strategy == "auto":
        builtin = BUILTIN.get(app_cfg.bundle_id)
        if builtin is not None:
            return builtin(app_el, app_cfg.name)
        return _extract_heading(app_el, app_cfg.name) or _extract_window_title(app_el, app_cfg.name)
    if strategy == "heading":
        return _extract_heading(app_el, app_cfg.name)
    if strategy == "window_title":
        return _extract_window_title(app_el, app_cfg.name)
    return None
