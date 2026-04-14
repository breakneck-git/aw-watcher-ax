"""Per-app context extraction strategies.

A strategy takes an AXUIElement for the running app process and returns a
short string (chat name, conversation title, file name) or None if nothing
useful could be extracted.

Built-in extractors in BUILTIN embed app-specific heuristics. Generic
fallbacks - `_extract_heading` and `_extract_window_title` - work for most
apps and are selectable via the `strategy` field in config.
"""

from collections.abc import Callable
from typing import Any

from .ax_utils import ax_get, ax_set, ax_walk, create_app_element
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


def _extract_claude(app_el: Any, app_name: str) -> str | None:
    # Claude Desktop is an Electron app. Its chat-header toolbar contains an
    # AXPopUpButton with description "Session options" sitting next to the
    # chat title button. Walk the tree looking for any container whose direct
    # children include that popup; the last AXButton with a non-empty title
    # before it in the sibling order is the chat title.
    for container in ax_walk(app_el, max_depth=25):
        children = ax_get(container, "AXChildren") or []
        last_title: str | None = None
        found_anchor = False
        for child in children:
            role = ax_get(child, "AXRole")
            if role == "AXButton":
                title = _clean(ax_get(child, "AXTitle"), app_name)
                if title:
                    last_title = title
            elif role == "AXPopUpButton" and ax_get(child, "AXDescription") == "Session options":
                found_anchor = True
                break
        if found_anchor and last_title:
            return last_title
    return None


BUILTIN: dict[str, Callable[[Any, str], str | None]] = {
    "com.anthropic.claudefordesktop": _extract_claude,
}


def extract_context(app_cfg: AppConfig, pid: int) -> str | None:
    app_el = create_app_element(pid)
    if app_el is None:
        return None

    # Electron/Chromium apps leave their accessibility tree empty until a
    # client explicitly asks for it. Flipping AXManualAccessibility causes
    # Chromium to build the tree; the setting persists for the life of the
    # process, so subsequent polls see a populated tree. On native macOS
    # apps the attribute is unsupported and the call is a harmless no-op.
    ax_set(app_el, "AXManualAccessibility", True)

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
