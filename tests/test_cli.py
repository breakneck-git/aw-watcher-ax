from unittest.mock import MagicMock

import pytest

from aw_watcher_ax import cli, watcher
from aw_watcher_ax.config import AppConfig, Config


@pytest.fixture
def valid_cfg() -> Config:
    return Config(
        poll_interval_sec=60,
        pulsetime_sec=180,
        aw_base_url="http://localhost:5600",
        apps=[AppConfig(bundle_id="x", name="X")],
    )


@pytest.fixture
def mock_requests(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock()
    mock.post.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())
    monkeypatch.setattr(watcher, "requests", mock)
    return mock


def test_main_returns_2_on_missing_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "load_config", MagicMock(side_effect=FileNotFoundError("nope")))
    assert cli.main(["--once"]) == 2


def test_main_returns_2_on_invalid_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "load_config", MagicMock(side_effect=ValueError("bad toml")))
    assert cli.main(["--once"]) == 2


def test_main_returns_2_on_malformed_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    import tomllib

    monkeypatch.setattr(
        cli, "load_config", MagicMock(side_effect=tomllib.TOMLDecodeError("bad", "x", 0))
    )
    assert cli.main(["--once"]) == 2


def test_main_returns_3_on_permission_denied_once(
    monkeypatch: pytest.MonkeyPatch, valid_cfg: Config, mock_requests: MagicMock
) -> None:
    monkeypatch.setattr(cli, "load_config", lambda: valid_cfg)
    monkeypatch.setattr(watcher, "check_accessibility_permission", lambda *, prompt: False)
    assert cli.main(["--once"]) == 3


def test_main_returns_0_on_normal_once(
    monkeypatch: pytest.MonkeyPatch, valid_cfg: Config, mock_requests: MagicMock
) -> None:
    monkeypatch.setattr(cli, "load_config", lambda: valid_cfg)
    monkeypatch.setattr(watcher, "check_accessibility_permission", lambda *, prompt: True)
    monkeypatch.setattr(watcher, "get_focused_app", lambda: None)
    assert cli.main(["--once"]) == 0


def test_main_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "0.2.0" in (captured.out + captured.err)
