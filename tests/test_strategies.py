from typing import Any

import pytest

from aw_watcher_ax import strategies
from aw_watcher_ax.config import AppConfig


class FakeElement:
    """In-memory stand-in for an AXUIElement, with dict-backed attributes."""

    def __init__(
        self, attrs: dict[str, Any] | None = None, children: list["FakeElement"] | None = None
    ) -> None:
        self.attrs = attrs or {}
        self.children = children or []


def _fake_ax_get(elem: Any, attr: str) -> Any:
    if elem is None:
        return None
    if attr == "AXChildren":
        return list(elem.children)
    return elem.attrs.get(attr)


def _fake_ax_walk(elem: Any, *, role: str | None = None, max_depth: int = 10):
    def _walk(el: FakeElement, depth: int):
        if depth > max_depth:
            return
        if role is None or el.attrs.get("AXRole") == role:
            yield el
        for child in el.children:
            yield from _walk(child, depth + 1)

    yield from _walk(elem, 0)


@pytest.fixture(autouse=True)
def _patch_ax(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(strategies, "ax_get", _fake_ax_get)
    monkeypatch.setattr(strategies, "ax_walk", _fake_ax_walk)
    monkeypatch.setattr(strategies, "ax_set", lambda _el, _attr, _val: 0)
    monkeypatch.setattr(strategies, "create_app_element", lambda _pid: _APP_EL)


_APP_EL: FakeElement | None = None


def _set_app(el: FakeElement) -> None:
    global _APP_EL
    _APP_EL = el


# ---------- window_title strategy ----------


def test_window_title_returns_title_when_present() -> None:
    win = FakeElement({"AXTitle": "My Document.py — MyProject"})
    app = FakeElement({"AXFocusedWindow": win})
    _set_app(app)

    cfg = AppConfig(bundle_id="dev.zed.Zed", name="Zed", strategy="window_title")
    assert strategies.extract_context(cfg, pid=0) == "My Document.py — MyProject"


def test_window_title_none_when_equals_app_name() -> None:
    win = FakeElement({"AXTitle": "Claude"})
    app = FakeElement({"AXFocusedWindow": win})
    _set_app(app)

    cfg = AppConfig(bundle_id="x", name="Claude", strategy="window_title")
    assert strategies.extract_context(cfg, pid=0) is None


def test_window_title_none_when_empty() -> None:
    win = FakeElement({"AXTitle": "   "})
    app = FakeElement({"AXFocusedWindow": win})
    _set_app(app)

    cfg = AppConfig(bundle_id="x", name="App", strategy="window_title")
    assert strategies.extract_context(cfg, pid=0) is None


def test_window_title_none_when_no_focused_window() -> None:
    app = FakeElement({})  # no AXFocusedWindow
    _set_app(app)

    cfg = AppConfig(bundle_id="x", name="App", strategy="window_title")
    assert strategies.extract_context(cfg, pid=0) is None


def test_window_title_truncated_to_max_len() -> None:
    long = "a" * 500
    win = FakeElement({"AXTitle": long})
    app = FakeElement({"AXFocusedWindow": win})
    _set_app(app)

    cfg = AppConfig(bundle_id="x", name="App", strategy="window_title")
    result = strategies.extract_context(cfg, pid=0)
    assert result is not None
    assert len(result) == 200


# ---------- heading strategy ----------


def test_heading_finds_first_axheading() -> None:
    heading = FakeElement({"AXRole": "AXHeading", "AXTitle": "How do cats purr?"})
    other = FakeElement({"AXRole": "AXStaticText", "AXTitle": "Sidebar link"})
    win = FakeElement({}, children=[other, heading])
    app = FakeElement({"AXFocusedWindow": win})
    _set_app(app)

    cfg = AppConfig(bundle_id="x", name="App", strategy="heading")
    assert strategies.extract_context(cfg, pid=0) == "How do cats purr?"


def test_heading_reads_value_when_title_missing() -> None:
    heading = FakeElement({"AXRole": "AXHeading", "AXValue": "Dinner plans"})
    win = FakeElement({}, children=[heading])
    app = FakeElement({"AXFocusedWindow": win})
    _set_app(app)

    cfg = AppConfig(bundle_id="x", name="App", strategy="heading")
    assert strategies.extract_context(cfg, pid=0) == "Dinner plans"


def test_heading_none_when_no_heading() -> None:
    win = FakeElement(
        {},
        children=[FakeElement({"AXRole": "AXStaticText", "AXTitle": "hi"})],
    )
    app = FakeElement({"AXFocusedWindow": win})
    _set_app(app)

    cfg = AppConfig(bundle_id="x", name="App", strategy="heading")
    assert strategies.extract_context(cfg, pid=0) is None


def test_heading_skips_when_matches_app_name() -> None:
    bad = FakeElement({"AXRole": "AXHeading", "AXTitle": "Claude"})
    good = FakeElement({"AXRole": "AXHeading", "AXTitle": "Actual conversation"})
    win = FakeElement({}, children=[bad, good])
    app = FakeElement({"AXFocusedWindow": win})
    _set_app(app)

    cfg = AppConfig(bundle_id="x", name="Claude", strategy="heading")
    assert strategies.extract_context(cfg, pid=0) == "Actual conversation"


# ---------- auto strategy dispatch ----------


def test_auto_for_claude_uses_claude_builtin() -> None:
    title_btn = FakeElement({"AXRole": "AXButton", "AXTitle": "rpi2b"})
    session_popup = FakeElement({"AXRole": "AXPopUpButton", "AXDescription": "Session options"})
    toolbar = FakeElement({"AXRole": "AXGroup"}, children=[title_btn, session_popup])
    win = FakeElement({}, children=[toolbar])
    app = FakeElement({"AXFocusedWindow": win}, children=[win])
    _set_app(app)

    cfg = AppConfig(
        bundle_id="com.anthropic.claudefordesktop",
        name="Claude",
        strategy="auto",
    )
    assert strategies.extract_context(cfg, pid=0) == "rpi2b"


def test_auto_falls_through_heading_then_window_title() -> None:
    win = FakeElement({"AXTitle": "Important doc"})
    app = FakeElement({"AXFocusedWindow": win})
    _set_app(app)

    cfg = AppConfig(bundle_id="unknown.app", name="Unknown", strategy="auto")
    assert strategies.extract_context(cfg, pid=0) == "Important doc"


def test_auto_returns_none_when_nothing_useful() -> None:
    win = FakeElement({"AXTitle": "Unknown"})
    app = FakeElement({"AXFocusedWindow": win})
    _set_app(app)

    cfg = AppConfig(bundle_id="x", name="Unknown", strategy="auto")
    assert strategies.extract_context(cfg, pid=0) is None


# ---------- claude built-in ----------


def test_claude_returns_none_without_session_options_anchor() -> None:
    btn = FakeElement({"AXRole": "AXButton", "AXTitle": "Some button"})
    win = FakeElement({}, children=[btn])
    app = FakeElement({"AXFocusedWindow": win}, children=[win])
    _set_app(app)

    cfg = AppConfig(
        bundle_id="com.anthropic.claudefordesktop",
        name="Claude",
        strategy="auto",
    )
    assert strategies.extract_context(cfg, pid=0) is None


def test_claude_skips_preceding_untitled_buttons() -> None:
    # User-menu popup before the chat title, then the title, then the anchor
    menu = FakeElement({"AXRole": "AXPopUpButton", "AXDescription": "Kirill, Settings"})
    title_btn = FakeElement({"AXRole": "AXButton", "AXTitle": "project chat"})
    session_popup = FakeElement({"AXRole": "AXPopUpButton", "AXDescription": "Session options"})
    # A trailing Preview button (must not leak into the result)
    preview = FakeElement({"AXRole": "AXButton", "AXTitle": "Preview"})
    toolbar = FakeElement({"AXRole": "AXGroup"}, children=[menu, title_btn, session_popup, preview])
    win = FakeElement({}, children=[toolbar])
    app = FakeElement({"AXFocusedWindow": win}, children=[win])
    _set_app(app)

    cfg = AppConfig(
        bundle_id="com.anthropic.claudefordesktop",
        name="Claude",
        strategy="auto",
    )
    assert strategies.extract_context(cfg, pid=0) == "project chat"


def test_claude_returns_none_when_anchor_has_only_empty_buttons() -> None:
    btn1 = FakeElement({"AXRole": "AXButton", "AXTitle": None})
    btn2 = FakeElement({"AXRole": "AXButton", "AXTitle": ""})
    btn3 = FakeElement({"AXRole": "AXButton", "AXTitle": "   "})
    session_popup = FakeElement({"AXRole": "AXPopUpButton", "AXDescription": "Session options"})
    toolbar = FakeElement({"AXRole": "AXGroup"}, children=[btn1, btn2, btn3, session_popup])
    win = FakeElement({}, children=[toolbar])
    app = FakeElement({"AXFocusedWindow": win}, children=[win])
    _set_app(app)

    cfg = AppConfig(
        bundle_id="com.anthropic.claudefordesktop",
        name="Claude",
        strategy="auto",
    )
    assert strategies.extract_context(cfg, pid=0) is None


# ---------- AXManualAccessibility flip ----------


def test_extract_context_flips_axmanualaccessibility_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, str, Any]] = []

    def spy(elem: Any, attr: str, value: Any) -> int:
        calls.append((elem, attr, value))
        return 0

    monkeypatch.setattr(strategies, "ax_set", spy)

    win = FakeElement({"AXTitle": "Some doc"})
    app = FakeElement({"AXFocusedWindow": win})
    _set_app(app)

    cfg = AppConfig(bundle_id="unknown.app", name="Unknown", strategy="window_title")
    strategies.extract_context(cfg, pid=0)

    assert calls == [(app, "AXManualAccessibility", True)]
