import textwrap
from pathlib import Path

import pytest

from aw_watcher_ax.config import AppConfig, load_config


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(textwrap.dedent(body))
    return p


def test_load_minimal(tmp_path: Path) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            """
        [[apps]]
        bundle_id = "com.anthropic.claudefordesktop"
        name = "Claude"
    """,
        )
    )
    assert cfg.poll_interval_sec == 60
    assert cfg.pulsetime_sec == 180
    assert cfg.aw_base_url == "http://localhost:5600"
    assert cfg.apps == [
        AppConfig(
            bundle_id="com.anthropic.claudefordesktop",
            name="Claude",
            strategy="auto",
        )
    ]


def test_load_overrides(tmp_path: Path) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            """
        poll_interval_sec = 30
        pulsetime_sec = 90
        aw_base_url = "http://localhost:6000"

        [[apps]]
        bundle_id = "dev.zed.Zed"
        name = "Zed"
        strategy = "window_title"
    """,
        )
    )
    assert cfg.poll_interval_sec == 30
    assert cfg.pulsetime_sec == 90
    assert cfg.aw_base_url == "http://localhost:6000"
    assert cfg.apps[0].strategy == "window_title"


def test_apps_by_bundle(tmp_path: Path) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            """
        [[apps]]
        bundle_id = "com.anthropic.claudefordesktop"
        name = "Claude"

        [[apps]]
        bundle_id = "ru.keepcoder.Telegram"
        name = "Telegram"
    """,
        )
    )
    mapping = cfg.apps_by_bundle
    assert set(mapping) == {
        "com.anthropic.claudefordesktop",
        "ru.keepcoder.Telegram",
    }
    assert mapping["ru.keepcoder.Telegram"].name == "Telegram"


def test_missing_fields_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bundle_id"):
        load_config(
            _write(
                tmp_path,
                """
            [[apps]]
            name = "Claude"
        """,
            )
        )


def test_invalid_strategy_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid strategy"):
        load_config(
            _write(
                tmp_path,
                """
            [[apps]]
            bundle_id = "dev.zed.Zed"
            name = "Zed"
            strategy = "magic"
        """,
            )
        )


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")


def test_rejects_zero_poll_interval(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="poll_interval_sec"):
        load_config(
            _write(
                tmp_path,
                """
            poll_interval_sec = 0

            [[apps]]
            bundle_id = "dev.zed.Zed"
            name = "Zed"
        """,
            )
        )


def test_rejects_negative_pulsetime(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        load_config(
            _write(
                tmp_path,
                """
            pulsetime_sec = -5

            [[apps]]
            bundle_id = "dev.zed.Zed"
            name = "Zed"
        """,
            )
        )


def test_rejects_pulsetime_too_small_for_poll(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"must be >= 2 \*"):
        load_config(
            _write(
                tmp_path,
                """
            poll_interval_sec = 60
            pulsetime_sec = 100

            [[apps]]
            bundle_id = "dev.zed.Zed"
            name = "Zed"
        """,
            )
        )


def test_accepts_pulsetime_exactly_double(tmp_path: Path) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            """
        poll_interval_sec = 60
        pulsetime_sec = 120

        [[apps]]
        bundle_id = "dev.zed.Zed"
        name = "Zed"
    """,
        )
    )
    assert cfg.pulsetime_sec == 120
    assert cfg.poll_interval_sec == 60


def test_rejects_duplicate_bundle_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate bundle_id"):
        load_config(
            _write(
                tmp_path,
                """
            [[apps]]
            bundle_id = "dev.zed.Zed"
            name = "Zed"

            [[apps]]
            bundle_id = "dev.zed.Zed"
            name = "Zed Preview"
        """,
            )
        )


def test_rejects_empty_apps(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one"):
        load_config(_write(tmp_path, "poll_interval_sec = 60\n"))


def test_rejects_empty_bundle_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bundle_id"):
        load_config(
            _write(
                tmp_path,
                """
            [[apps]]
            bundle_id = ""
            name = "Claude"
        """,
            )
        )


def test_rejects_whitespace_bundle_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bundle_id"):
        load_config(
            _write(
                tmp_path,
                """
            [[apps]]
            bundle_id = "   "
            name = "Claude"
        """,
            )
        )


def test_rejects_empty_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="name"):
        load_config(
            _write(
                tmp_path,
                """
            [[apps]]
            bundle_id = "com.anthropic.claudefordesktop"
            name = ""
        """,
            )
        )


def test_rejects_whitespace_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="name"):
        load_config(
            _write(
                tmp_path,
                """
            [[apps]]
            bundle_id = "com.anthropic.claudefordesktop"
            name = "   "
        """,
            )
        )


def test_rejects_non_string_bundle_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bundle_id"):
        load_config(
            _write(
                tmp_path,
                """
            [[apps]]
            bundle_id = 123
            name = "Claude"
        """,
            )
        )


def test_rejects_non_string_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="name"):
        load_config(
            _write(
                tmp_path,
                """
            [[apps]]
            bundle_id = "com.anthropic.claudefordesktop"
            name = 42
        """,
            )
        )
