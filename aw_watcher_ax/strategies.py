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
_SESSION_DESCS = ("Session actions", "Session options")  # Claude Code sessions
_MORE_PREFIX = "More options for "  # regular Chat / Cowork header popup


def _clean(value: Any, app_name: str) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s == app_name:
        return None
    return s[:_MAX_LEN]


def _node_text(el: Any, app_name: str) -> str | None:
    """Return the first non-empty `_clean`-ed text on an element.

    Checks AXTitle first (common for AXButton and similar) then AXValue
    (common for AXStaticText). Both paths in `_extract_claude` use this so
    the priority order is identical whether we're reading the sibling
    itself or a descendant.
    """
    return _clean(ax_get(el, "AXTitle"), app_name) or _clean(ax_get(el, "AXValue"), app_name)


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


def _is_anchor(el: Any) -> bool:
    """True if `el` is the AXPopUpButton that sits right after the chat title."""
    if ax_get(el, "AXRole") != "AXPopUpButton":
        return False
    desc = ax_get(el, "AXDescription") or ""
    return desc in _SESSION_DESCS or desc.startswith(_MORE_PREFIX)


def _find_title_sibling(container: Any) -> Any | None:
    """Return the element immediately before the anchor popup, or None.

    Sidebar entries nest the popup inside an extra AXGroup, so the popup
    appears at index 0 in its immediate parent and is skipped.
    """
    children = ax_get(container, "AXChildren") or []
    for i, child in enumerate(children):
        if _is_anchor(child) and i > 0:
            return children[i - 1]
    return None


def _extract_claude(app_el: Any, app_name: str) -> str | None:
    # Start from AXFocusedWindow, not app_el — app_el.AXChildren yields
    # shallow window proxies that don't descend into the web content.
    win = ax_get(app_el, "AXFocusedWindow")
    if win is None:
        return None
    for container in ax_walk(win, max_depth=30):
        prev = _find_title_sibling(container)
        if prev is None:
            continue
        for el in ax_walk(prev, max_depth=4):
            text = _node_text(el, app_name)
            if text:
                return text
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
