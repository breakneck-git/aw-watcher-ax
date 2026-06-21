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
_TITLE_UP_LEVELS = 4  # how far up from the anchor to look for the title's row
_TITLE_DEPTH = 5  # how deep within a row to search for the title text


def _clean(value: Any, app_name: str) -> str | None:
    # AX text attributes (window titles, chat names) arrive as pyobjc str
    # subclasses. A non-string value is never a context title — most notably an
    # AXCheckBox's integer AXValue of 0, which str() would turn into the literal
    # "0" that leaked into every Claude heartbeat. Reject anything non-string.
    if not isinstance(value, str):
        return None
    s = value.strip()
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
        cleaned = _node_text(el, app_name)
        if cleaned:
            return cleaned
    return None


def _is_anchor(el: Any) -> bool:
    """True if `el` is the AXPopUpButton that sits right after the chat title."""
    if ax_get(el, "AXRole") != "AXPopUpButton":
        return False
    desc = ax_get(el, "AXDescription") or ""
    return desc in _SESSION_DESCS or desc.startswith(_MORE_PREFIX)


_SKIP_ROLES = ("AXPopUpButton", "AXCheckBox")  # menus and view-toggles, never the title


def _title_before_anchor(row: Any, anchor_desc: str, app_name: str) -> str | None:
    """Return the last title-bearing text in `row` that precedes the anchor.

    The conversation title sits just before the anchor in the header row, but
    so do controls that are never the title: the user-profile menu and model
    picker (AXPopUpButton) and the Terminal/Diff/Preview view-toggles
    (AXCheckBox). Skip those by role and stop at the anchor, matched by its
    AXDescription. "Last before the anchor" beats "first in the row" because
    earlier elements include the profile button.
    """
    last: str | None = None
    for el in ax_walk(row, max_depth=_TITLE_DEPTH):
        if ax_get(el, "AXRole") in _SKIP_ROLES:
            # The anchor is an AXPopUpButton; stop the scan when we reach it.
            if (ax_get(el, "AXDescription") or "") == anchor_desc:
                return last
            continue
        text = _node_text(el, app_name)
        if text:
            last = text
    return last


def _extract_claude(app_el: Any, app_name: str) -> str | None:
    # Start from AXFocusedWindow, not app_el — app_el.AXChildren yields
    # shallow window proxies that don't descend into the web content.
    win = ax_get(app_el, "AXFocusedWindow")
    if win is None:
        return None
    for container in ax_walk(win, max_depth=30):
        children = ax_get(container, "AXChildren") or []
        for i, child in enumerate(children):
            if not _is_anchor(child):
                continue
            desc = ax_get(child, "AXDescription") or ""
            # Chat / Cowork: the title is embedded in "More options for <title>".
            if desc.startswith(_MORE_PREFIX):
                # Sidebar rows wrap this popup in an extra AXGroup so it lands at
                # index 0; the active header's popup is a later sibling. The same
                # "More options for X" text appears on both, so only index > 0
                # distinguishes the active conversation from a sidebar entry.
                if i == 0:
                    continue
                title = _clean(desc[len(_MORE_PREFIX) :], app_name)
                if title:
                    return title
                continue
            # Claude Code sessions ("Session actions"/"Session options"): this
            # anchor only ever appears in the active header (sidebar sessions use
            # "More options for X"), so there is no sidebar collision and it is
            # matched at any index. The title is a text element in the header row
            # holding the anchor — newer layouts put it in a sibling group rather
            # than directly before the anchor, so walk up until a row yields one.
            node = child
            for _ in range(_TITLE_UP_LEVELS):
                parent = ax_get(node, "AXParent")
                if parent is None:
                    break
                title = _title_before_anchor(parent, desc, app_name)
                if title:
                    return title
                node = parent
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
