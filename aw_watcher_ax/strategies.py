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


def _extract_claude(app_el: Any, app_name: str) -> str | None:
    # Claude Desktop renders the chat header deep inside a Chromium/React tree.
    # Two things the walk must handle:
    #
    # 1. Start from AXFocusedWindow, not app_el. Walking app_el.AXChildren
    #    yields shallow window proxies that don't descend into the web content
    #    — their subtree tops out around depth 6, while the real header sits
    #    ~24 levels below the focused window.
    # 2. The anchor is an AXPopUpButton whose AXDescription is "Session actions"
    #    (Claude 1.2581+; previously "Session options"). It sits immediately
    #    after the element that holds the chat title. That element is either
    #    an AXButton with AXTitle (older layouts) or an AXGroup containing an
    #    AXStaticText value (current layout, where a user-profile AXButton
    #    sits earlier in the sibling list and would otherwise be mis-picked).
    #    We find the anchor and read the *immediately preceding* sibling.
    win = ax_get(app_el, "AXFocusedWindow")
    if win is None:
        return None
    anchor_descs = ("Session actions", "Session options")
    for container in ax_walk(win, max_depth=30):
        children = ax_get(container, "AXChildren") or []
        anchor_idx = -1
        for i, child in enumerate(children):
            if (
                ax_get(child, "AXRole") == "AXPopUpButton"
                and ax_get(child, "AXDescription") in anchor_descs
            ):
                anchor_idx = i
                break
        if anchor_idx <= 0:
            continue
        prev = children[anchor_idx - 1]
        # Try the sibling itself and up to 4 levels of descendants. In current
        # Claude the title is an AXStaticText nested 1 AXGroup deep; the cap
        # bounds accidental wandering into unrelated header content if Claude
        # ships a future layout that nests deeper.
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
