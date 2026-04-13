from unittest.mock import MagicMock

import pytest

from aw_watcher_ax import watcher
from aw_watcher_ax.config import AppConfig, Config


@pytest.fixture
def cfg() -> Config:
    return Config(
        poll_interval_sec=60,
        pulsetime_sec=180,
        aw_base_url="http://localhost:5600",
        apps=[
            AppConfig(
                bundle_id="com.anthropic.claudefordesktop",
                name="Claude",
                strategy="auto",
            )
        ],
    )


@pytest.fixture
def requests_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock()
    mock.post.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())
    monkeypatch.setattr(watcher, "requests", mock)
    return mock


@pytest.fixture(autouse=True)
def _grant_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watcher, "check_accessibility_permission", lambda *, prompt: True)


def test_once_mode_emits_heartbeat_for_monitored_app(
    monkeypatch: pytest.MonkeyPatch, cfg: Config, requests_mock: MagicMock
) -> None:
    monkeypatch.setattr(
        watcher, "get_focused_app", lambda: (1234, "com.anthropic.claudefordesktop")
    )
    monkeypatch.setattr(watcher, "extract_context", lambda app_cfg, pid: "Hello there")

    watcher.run(cfg, once=True)

    heartbeat_calls = [
        call for call in requests_mock.post.call_args_list if "/heartbeat" in call[0][0]
    ]
    assert len(heartbeat_calls) == 1
    call = heartbeat_calls[0]
    assert call[1]["params"]["pulsetime"] == 180
    body = call[1]["json"]
    assert body["data"] == {"app": "Claude", "context": "Hello there"}
    assert body["duration"] == 0
    assert "timestamp" in body


def test_once_mode_skips_unmonitored_app(
    monkeypatch: pytest.MonkeyPatch, cfg: Config, requests_mock: MagicMock
) -> None:
    monkeypatch.setattr(watcher, "get_focused_app", lambda: (1, "com.apple.Finder"))
    monkeypatch.setattr(
        watcher, "extract_context", MagicMock(side_effect=AssertionError("should not be called"))
    )

    watcher.run(cfg, once=True)

    heartbeat_calls = [
        call for call in requests_mock.post.call_args_list if "/heartbeat" in call[0][0]
    ]
    assert heartbeat_calls == []


def test_once_mode_skips_when_context_is_none(
    monkeypatch: pytest.MonkeyPatch, cfg: Config, requests_mock: MagicMock
) -> None:
    monkeypatch.setattr(watcher, "get_focused_app", lambda: (1, "com.anthropic.claudefordesktop"))
    monkeypatch.setattr(watcher, "extract_context", lambda app_cfg, pid: None)

    watcher.run(cfg, once=True)

    heartbeat_calls = [
        call for call in requests_mock.post.call_args_list if "/heartbeat" in call[0][0]
    ]
    assert heartbeat_calls == []


def test_once_mode_ensures_bucket_before_polling(
    monkeypatch: pytest.MonkeyPatch, cfg: Config, requests_mock: MagicMock
) -> None:
    monkeypatch.setattr(watcher, "get_focused_app", lambda: None)
    monkeypatch.setattr(watcher, "extract_context", lambda *a, **k: None)

    watcher.run(cfg, once=True)

    bucket_create_calls = [
        call
        for call in requests_mock.post.call_args_list
        if call[0][0].endswith("/api/0/buckets/aw-watcher-ax_" + __import__("socket").gethostname())
    ]
    assert len(bucket_create_calls) == 1
    payload = bucket_create_calls[0][1]["json"]
    assert payload["client"] == "aw-watcher-ax"
    assert payload["type"] == "currentwindow"


def test_once_mode_without_permission_returns_early(
    monkeypatch: pytest.MonkeyPatch, cfg: Config, requests_mock: MagicMock
) -> None:
    monkeypatch.setattr(watcher, "check_accessibility_permission", lambda *, prompt: False)

    watcher.run(cfg, once=True)

    assert requests_mock.post.call_args_list == []


def test_ensure_bucket_with_retry_succeeds_after_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import requests as real_requests

    call_count = {"n": 0}

    def flaky_post(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise real_requests.ConnectionError("AW not up yet")
        return MagicMock(status_code=200, raise_for_status=MagicMock())

    mock_requests = MagicMock()
    mock_requests.post.side_effect = flaky_post
    monkeypatch.setattr(watcher, "requests", mock_requests)

    sleeps: list[float] = []
    fake_time = MagicMock()
    fake_time.sleep = lambda s: sleeps.append(s)
    monkeypatch.setattr(watcher, "time", fake_time)

    watcher._ensure_bucket_with_retry("http://x", "bucket", max_attempts=None)

    assert call_count["n"] == 3
    assert sleeps == [1.0, 2.0]


def test_ensure_bucket_with_retry_gives_up_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import requests as real_requests

    mock_requests = MagicMock()
    mock_requests.post.side_effect = real_requests.ConnectionError("down")
    monkeypatch.setattr(watcher, "requests", mock_requests)

    fake_time = MagicMock()
    fake_time.sleep = lambda s: None
    monkeypatch.setattr(watcher, "time", fake_time)

    with pytest.raises(real_requests.ConnectionError):
        watcher._ensure_bucket_with_retry("http://x", "bucket", max_attempts=3)

    assert mock_requests.post.call_count == 3
