"""Low-level macOS Accessibility API helpers.

Pyobjc is imported lazily inside each function so this module can be imported
on non-macOS platforms without raising (keeps strategies.py unit-testable
anywhere). Any call to an API function without pyobjc installed raises
RuntimeError.
"""

from collections.abc import Iterator
from typing import Any

_MACOS_ONLY = "aw-watcher-ax is macOS-only (pyobjc not available)"


def check_accessibility_permission(*, prompt: bool) -> bool:
    """Return True if this process has Accessibility permission.

    If `prompt` is True and permission is missing, macOS shows the system
    prompt asking the user to add this binary under
    System Settings → Privacy & Security → Accessibility. The result updates
    without a restart once the user grants permission.
    """
    try:
        from ApplicationServices import (  # type: ignore[import-not-found]
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
    except ImportError as e:
        raise RuntimeError(_MACOS_ONLY) from e
    return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt}))


def get_focused_app() -> tuple[int, str] | None:
    """Return (pid, bundle_id) of the frontmost app, or None if unavailable."""
    try:
        from AppKit import NSWorkspace  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(_MACOS_ONLY) from e
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    bundle_id = app.bundleIdentifier()
    if bundle_id is None:
        return None
    return (int(app.processIdentifier()), str(bundle_id))


def create_app_element(pid: int) -> Any:
    try:
        from ApplicationServices import (  # type: ignore[import-not-found]
            AXUIElementCreateApplication,
        )
    except ImportError as e:
        raise RuntimeError(_MACOS_ONLY) from e
    return AXUIElementCreateApplication(pid)


def ax_get(elem: Any, attr: str) -> Any:
    """Read an AX attribute from `elem`. Returns None on any error."""
    try:
        from ApplicationServices import (  # type: ignore[import-not-found]
            AXUIElementCopyAttributeValue,
        )
    except ImportError as e:
        raise RuntimeError(_MACOS_ONLY) from e
    err, value = AXUIElementCopyAttributeValue(elem, attr, None)
    if err != 0:
        return None
    return value


def ax_set(elem: Any, attr: str, value: Any) -> int:
    """Set an AX attribute on `elem`, returning the raw AXError code.

    This is used to flip `AXManualAccessibility=True` on Electron/Chromium
    apps so they build their accessibility tree on demand. Native macOS apps
    reject the attribute (non-zero error) and callers ignore the return
    value - the call is effectively a no-op on non-Chromium apps.
    """
    try:
        from ApplicationServices import (  # type: ignore[import-not-found]
            AXUIElementSetAttributeValue,
        )
    except ImportError as e:
        raise RuntimeError(_MACOS_ONLY) from e
    return AXUIElementSetAttributeValue(elem, attr, value)


def ax_walk(elem: Any, *, role: str | None = None, max_depth: int = 10) -> Iterator[Any]:
    """Depth-first walk over an AX element tree, yielding matching elements.

    If `role` is given, only elements whose AXRole equals `role` are yielded,
    but the walk still descends into non-matching elements to find nested
    matches.
    """

    def _walk(el: Any, depth: int) -> Iterator[Any]:
        if depth > max_depth:
            return
        if role is None or ax_get(el, "AXRole") == role:
            yield el
        children = ax_get(el, "AXChildren") or []
        for child in children:
            yield from _walk(child, depth + 1)

    yield from _walk(elem, 0)
