import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from aw_watcher_ax import watcher
from aw_watcher_ax.config import AppConfig, Config


def _fake_ps(stdout=None, exc=None):
    def run(*_a, **_k):
        if exc is not None:
            raise exc
        return SimpleNamespace(stdout=stdout)

    return run


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


@pytest.fixture(autouse=True)
def _isolate_lock(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # Point the single-instance lock at a per-test temp path so daemon-mode
    # tests acquire a real (uncontended) flock instead of fighting the live
    # daemon or each other.
    monkeypatch.setattr(watcher, "_LOCK_PATH", tmp_path / "watcher.lock")


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


def test_once_mode_reraises_request_exception_from_heartbeat(
    monkeypatch: pytest.MonkeyPatch, cfg: Config
) -> None:
    """Heartbeat failure in --once must not be swallowed to exit 0.

    A smoke test that can't deliver a heartbeat needs to surface the failure
    so the caller (cli.main) can map it to exit code 4. The daemon loop keeps
    its swallow-and-continue behavior; this assertion is only for --once.
    """
    import requests as real_requests

    def post_side_effect(url: str, **_kwargs):
        if "/heartbeat" in url:
            raise real_requests.ConnectionError("AW went away mid-heartbeat")
        return MagicMock(status_code=200, raise_for_status=MagicMock())

    mock = MagicMock()
    mock.post.side_effect = post_side_effect
    monkeypatch.setattr(watcher, "requests", mock)
    monkeypatch.setattr(
        watcher, "get_focused_app", lambda: (1234, "com.anthropic.claudefordesktop")
    )
    monkeypatch.setattr(watcher, "extract_context", lambda *_a, **_k: "some chat title")

    with pytest.raises(real_requests.ConnectionError):
        watcher.run(cfg, once=True)


def test_poll_once_recreates_bucket_and_retries_on_heartbeat_404(
    monkeypatch: pytest.MonkeyPatch, cfg: Config
) -> None:
    # If the AW bucket disappears (datastore reset/migration), the first
    # heartbeat 404s. The watcher must recreate the bucket and retry once,
    # rather than 404ing silently forever.
    import requests as real_requests

    calls: list[tuple[str, object]] = []  # (kind, json-body)

    def post(url: str, **kwargs):
        kind = (
            "heartbeat"
            if "/heartbeat" in url
            else ("bucket" if url.endswith("/buckets/" + watcher._bucket_id()) else "other")
        )
        calls.append((kind, kwargs.get("json")))
        if kind == "heartbeat":
            resp = MagicMock()
            if sum(1 for k, _ in calls if k == "heartbeat") == 1:
                err = real_requests.HTTPError("404 Not Found")
                err.response = MagicMock(status_code=404)
                resp.raise_for_status.side_effect = err
            else:
                resp.raise_for_status = MagicMock()
            return resp
        return MagicMock(status_code=200, raise_for_status=MagicMock())

    mock = MagicMock()
    mock.post.side_effect = post
    monkeypatch.setattr(watcher, "requests", mock)
    monkeypatch.setattr(
        watcher, "get_focused_app", lambda: (1234, "com.anthropic.claudefordesktop")
    )
    monkeypatch.setattr(watcher, "extract_context", lambda *_a, **_k: "chat title")

    assert watcher.run(cfg, once=True) == 0

    kinds = [k for k, _ in calls]
    assert kinds.count("heartbeat") == 2  # initial 404 + retry after recreation
    assert kinds.count("bucket") == 2  # startup + recovery
    # Recreation must happen BETWEEN the two heartbeats, not after.
    hb_idx = [i for i, k in enumerate(kinds) if k == "heartbeat"]
    bk_idx = [i for i, k in enumerate(kinds) if k == "bucket"]
    assert hb_idx[0] < bk_idx[1] < hb_idx[1]
    # The retried heartbeat must carry the SAME payload as the first.
    expected = {"app": "Claude", "context": "chat title"}
    hb_bodies = [b for k, b in calls if k == "heartbeat"]
    assert hb_bodies[1]["data"] == expected
    assert hb_bodies[0]["data"] == expected


def test_poll_once_does_not_retry_non_404_heartbeat_error(
    monkeypatch: pytest.MonkeyPatch, cfg: Config
) -> None:
    # A 500 (or any non-404) must NOT trigger bucket recreation; it propagates
    # like any other heartbeat failure (re-raised in --once mode).
    import requests as real_requests

    urls: list[str] = []

    def post(url: str, **_kwargs):
        urls.append(url)
        if "/heartbeat" in url:
            resp = MagicMock()
            err = real_requests.HTTPError("500 Server Error")
            err.response = MagicMock(status_code=500)
            resp.raise_for_status.side_effect = err
            return resp
        return MagicMock(status_code=200, raise_for_status=MagicMock())

    mock = MagicMock()
    mock.post.side_effect = post
    monkeypatch.setattr(watcher, "requests", mock)
    monkeypatch.setattr(
        watcher, "get_focused_app", lambda: (1234, "com.anthropic.claudefordesktop")
    )
    monkeypatch.setattr(watcher, "extract_context", lambda *_a, **_k: "chat title")

    with pytest.raises(real_requests.HTTPError):
        watcher.run(cfg, once=True)

    bucket_creates = [u for u in urls if u.endswith("/buckets/" + watcher._bucket_id())]
    assert len(bucket_creates) == 1  # startup only, no recovery attempt


def test_poll_once_reraises_404_with_no_response(
    monkeypatch: pytest.MonkeyPatch, cfg: Config
) -> None:
    # An HTTPError can carry no .response. That must propagate (re-raised in
    # --once), NOT be mistaken for a missing-bucket 404 and trigger recreation.
    import requests as real_requests

    urls: list[str] = []

    def post(url: str, **_kwargs):
        urls.append(url)
        if "/heartbeat" in url:
            resp = MagicMock()
            err = real_requests.HTTPError("no response attached")
            err.response = None
            resp.raise_for_status.side_effect = err
            return resp
        return MagicMock(status_code=200, raise_for_status=MagicMock())

    mock = MagicMock()
    mock.post.side_effect = post
    monkeypatch.setattr(watcher, "requests", mock)
    monkeypatch.setattr(
        watcher, "get_focused_app", lambda: (1234, "com.anthropic.claudefordesktop")
    )
    monkeypatch.setattr(watcher, "extract_context", lambda *_a, **_k: "chat title")

    with pytest.raises(real_requests.HTTPError):
        watcher.run(cfg, once=True)

    bucket_creates = [u for u in urls if u.endswith("/buckets/" + watcher._bucket_id())]
    assert len(bucket_creates) == 1  # startup only, no recovery attempt


def test_ensure_bucket_treats_304_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    # 304 = bucket already exists (AW idempotency). Must return without raising.
    mock = MagicMock()
    resp = MagicMock(status_code=304)
    resp.raise_for_status.side_effect = AssertionError("raise_for_status must not be called on 304")
    mock.post.return_value = resp
    monkeypatch.setattr(watcher, "requests", mock)

    watcher._ensure_bucket("http://x", "bucket")  # must not raise


def test_ensure_bucket_raises_on_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests as real_requests

    mock = MagicMock()
    resp = MagicMock(status_code=500)
    resp.raise_for_status.side_effect = real_requests.HTTPError("500 Server Error")
    mock.post.return_value = resp
    monkeypatch.setattr(watcher, "requests", mock)

    with pytest.raises(real_requests.HTTPError):
        watcher._ensure_bucket("http://x", "bucket")


def test_ensure_bucket_with_retry_caps_backoff_at_60(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pins both the forever-retry loop (max_attempts=None) and the 60s cap:
    # backoff doubles 1,2,4,...,32 then clamps at 60.
    import requests as real_requests

    calls = {"n": 0}

    def post(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] <= 8:
            raise real_requests.ConnectionError("AW not up yet")
        return MagicMock(status_code=200, raise_for_status=MagicMock())

    mock = MagicMock()
    mock.post.side_effect = post
    monkeypatch.setattr(watcher, "requests", mock)

    sleeps: list[float] = []
    fake_time = MagicMock()
    fake_time.sleep = lambda s: sleeps.append(s)
    monkeypatch.setattr(watcher, "time", fake_time)

    watcher._ensure_bucket_with_retry("http://x", "bucket", max_attempts=None)

    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


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


def test_wait_for_permission_daemon_mode_polls_until_granted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = iter([False, False, True])

    def check(*, prompt: bool) -> bool:
        return next(results)

    monkeypatch.setattr(watcher, "check_accessibility_permission", check)

    sleeps: list[float] = []
    fake_time = MagicMock()
    fake_time.sleep = lambda s: sleeps.append(s)
    monkeypatch.setattr(watcher, "time", fake_time)

    assert watcher._wait_for_permission(once=False) is True
    assert sleeps == [watcher._PERMISSION_RETRY_SEC]


def test_daemon_loop_swallows_exception_and_continues_polling(
    monkeypatch: pytest.MonkeyPatch, cfg: Config, requests_mock: MagicMock
) -> None:
    calls = {"n": 0}

    def flaky_poll(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient failure")
        if calls["n"] == 2:
            return
        raise KeyboardInterrupt

    monkeypatch.setattr(watcher, "_poll_once", flaky_poll)

    fake_time = MagicMock()
    fake_time.sleep = lambda _s: None
    monkeypatch.setattr(watcher, "time", fake_time)

    with pytest.raises(KeyboardInterrupt):
        watcher.run(cfg, once=False)

    assert calls["n"] == 3


def test_daemon_loop_blocks_when_permission_revoked_and_recovers(
    monkeypatch: pytest.MonkeyPatch, cfg: Config, requests_mock: MagicMock
) -> None:
    # Sequence of check_accessibility_permission() return values:
    # 1. initial _wait_for_permission (prompt=True) → True
    # 2. loop iter 1 pre-poll check (prompt=False) → False (revoked)
    # 3. _wait_for_permission (prompt=True) → False (still revoked)
    # 4. _wait_for_permission while (prompt=False) → False
    # 5. _wait_for_permission while (prompt=False) → True (regranted)
    # 6. loop iter 2 pre-poll check (prompt=False) → True
    # then _poll_once raises KeyboardInterrupt to exit
    perm_seq = iter([True, False, False, False, True, True])
    monkeypatch.setattr(watcher, "check_accessibility_permission", lambda *, prompt: next(perm_seq))

    poll_calls = {"n": 0}

    def poll(*_args, **_kwargs):
        poll_calls["n"] += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(watcher, "_poll_once", poll)

    fake_time = MagicMock()
    fake_time.sleep = lambda _s: None
    monkeypatch.setattr(watcher, "time", fake_time)

    with pytest.raises(KeyboardInterrupt):
        watcher.run(cfg, once=False)

    assert poll_calls["n"] == 1


def test_single_instance_lock_refuses_to_take_over_self(tmp_path) -> None:
    lock = tmp_path / "w.lock"
    fd1 = watcher._acquire_single_instance_lock(lock)
    assert fd1 is not None
    # The recorded holder is THIS process — newest-wins must not terminate self,
    # so a same-process second acquisition fails rather than killing the runner.
    assert watcher._acquire_single_instance_lock(lock) is None
    # Releasing the first lets a later acquisition succeed again.
    fd1.close()
    fd2 = watcher._acquire_single_instance_lock(lock)
    assert fd2 is not None
    fd2.close()


# A lock-holder subprocess. The leading marker makes its `ps` command line
# match _is_watcher_process; drop the marker to simulate an unrelated process
# that happens to hold a recycled PID written into the lock file.
def _holder_code(lock, *, watcher_marker: bool) -> str:
    marker = "# aw-watcher-ax\n" if watcher_marker else "# unrelated process\n"
    return (
        marker + "import fcntl, os, sys, time\n"
        f"f = open({str(lock)!r}, 'r+' if os.path.exists({str(lock)!r}) else 'w+')\n"
        "fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "f.seek(0); f.truncate(); f.write(str(os.getpid())); f.flush()\n"
        "sys.stdout.write('locked\\n'); sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )


def test_acquire_takes_over_from_another_process(tmp_path) -> None:
    # Newest wins: a stale/hung holder in ANOTHER process must be terminated and
    # the lock taken over, so a freshly launched daemon is never blocked by an
    # old one left running.
    import subprocess
    import sys

    lock = tmp_path / "w.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", _holder_code(lock, watcher_marker=True)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"  # holder owns the lock now
        handle = watcher._acquire_single_instance_lock(lock)
        assert handle is not None  # we took over
        holder.wait(timeout=5)  # the stale holder was terminated
        assert holder.returncode is not None
        handle.close()
    finally:
        if holder.poll() is None:
            holder.kill()


def test_acquire_does_not_kill_non_watcher_holder(tmp_path) -> None:
    # A recycled PID written into the lock file may belong to an unrelated
    # process. Never signal it: if the holder is not one of ours, give up.
    import subprocess
    import sys

    lock = tmp_path / "w.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", _holder_code(lock, watcher_marker=False)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        assert watcher._acquire_single_instance_lock(lock) is None  # refused to take over
        assert holder.poll() is None  # the unrelated holder was NOT killed
    finally:
        holder.kill()


def test_run_daemon_exits_5_when_lock_cannot_be_taken(
    monkeypatch: pytest.MonkeyPatch, cfg: Config, requests_mock: MagicMock
) -> None:
    # The lock is held by THIS process (newest-wins won't self-terminate), so
    # run() can't take it: it must return exit code 5 without polling.
    held = watcher._acquire_single_instance_lock(watcher._LOCK_PATH)
    assert held is not None
    polled = {"n": 0}

    def count_poll(*_a, **_k):
        polled["n"] += 1

    monkeypatch.setattr(watcher, "_poll_once", count_poll)
    try:
        assert watcher.run(cfg, once=False) == 5
    finally:
        held.close()
    assert polled["n"] == 0


def test_run_once_does_not_take_single_instance_lock(
    monkeypatch: pytest.MonkeyPatch, cfg: Config, requests_mock: MagicMock
) -> None:
    # A --once smoke test must run even while the daemon holds the lock.
    held = watcher._acquire_single_instance_lock(watcher._LOCK_PATH)
    assert held is not None
    monkeypatch.setattr(watcher, "get_focused_app", lambda: None)
    try:
        assert watcher.run(cfg, once=True) == 0
    finally:
        held.close()


# ---------- _is_watcher_process (PID-reuse guard) ----------


def test_is_watcher_process_matches_python_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        watcher.subprocess,
        "run",
        _fake_ps(stdout="/opt/py/Python /opt/py/Python /Users/x/.venv/bin/aw-watcher-ax\n"),
    )
    assert watcher._is_watcher_process(123) is True


def test_is_watcher_process_rejects_tail_over_logfile(monkeypatch: pytest.MonkeyPatch) -> None:
    # A recycled PID owned by `tail -f .../aw-watcher-ax/watcher.log` must NOT be
    # treated as the daemon just because the path contains "aw-watcher-ax".
    monkeypatch.setattr(
        watcher.subprocess,
        "run",
        _fake_ps(stdout="tail tail -f /Users/x/Library/Logs/aw-watcher-ax/watcher.log\n"),
    )
    assert watcher._is_watcher_process(123) is False


def test_is_watcher_process_rejects_pager_over_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        watcher.subprocess,
        "run",
        _fake_ps(stdout="less less /Users/x/.venv/bin/aw-watcher-ax\n"),
    )
    assert watcher._is_watcher_process(123) is False


def test_is_watcher_process_false_when_name_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        watcher.subprocess, "run", _fake_ps(stdout="python3.11 python3.11 -c pass\n")
    )
    assert watcher._is_watcher_process(123) is False


def test_is_watcher_process_false_on_ps_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watcher.subprocess, "run", _fake_ps(exc=OSError("boom")))
    assert watcher._is_watcher_process(123) is False


def test_is_watcher_process_false_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        watcher.subprocess, "run", _fake_ps(exc=subprocess.TimeoutExpired(cmd="ps", timeout=5))
    )
    assert watcher._is_watcher_process(123) is False


# ---------- _read_lock_pid ----------


def test_read_lock_pid_missing_file(tmp_path) -> None:
    assert watcher._read_lock_pid(tmp_path / "nope.lock") is None


def test_read_lock_pid_empty(tmp_path) -> None:
    p = tmp_path / "l"
    p.write_text("")
    assert watcher._read_lock_pid(p) is None


def test_read_lock_pid_garbage(tmp_path) -> None:
    p = tmp_path / "l"
    p.write_text("not-a-pid")
    assert watcher._read_lock_pid(p) is None


def test_read_lock_pid_valid(tmp_path) -> None:
    p = tmp_path / "l"
    p.write_text("  4321\n")
    assert watcher._read_lock_pid(p) == 4321


# ---------- _take_over escalation / failure ----------


def test_acquire_escalates_to_sigkill_when_sigterm_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # Holder ignores SIGTERM; takeover must escalate to SIGKILL and still win.
    monkeypatch.setattr(watcher, "_TAKEOVER_TICKS", 3)  # ~0.3s grace, keep test fast
    lock = tmp_path / "w.lock"
    code = (
        "# aw-watcher-ax\n"
        "import fcntl, os, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"f = open({str(lock)!r}, 'w+')\n"
        "fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "f.seek(0); f.truncate(); f.write(str(os.getpid())); f.flush()\n"
        "sys.stdout.write('locked\\n'); sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    holder = subprocess.Popen(
        [__import__("sys").executable, "-c", code], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        handle = watcher._acquire_single_instance_lock(lock)
        assert handle is not None  # SIGKILL won
        holder.wait(timeout=5)
        assert holder.returncode is not None
        handle.close()
    finally:
        if holder.poll() is None:
            holder.kill()


def test_acquire_returns_none_when_holder_will_not_die(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # A confirmed-watcher holder that survives all signals (here: os.kill no-op'd)
    # → _take_over fails → acquisition returns None (run() maps to exit 5).
    monkeypatch.setattr(watcher, "_TAKEOVER_TICKS", 2)
    monkeypatch.setattr(watcher.os, "kill", lambda *_a, **_k: None)  # signals do nothing
    lock = tmp_path / "w.lock"
    code = (
        "# aw-watcher-ax\n"
        "import fcntl, os, sys, time\n"
        f"f = open({str(lock)!r}, 'w+')\n"
        "fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "f.seek(0); f.truncate(); f.write(str(os.getpid())); f.flush()\n"
        "sys.stdout.write('locked\\n'); sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    holder = subprocess.Popen(
        [__import__("sys").executable, "-c", code], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        assert watcher._acquire_single_instance_lock(lock) is None
        assert holder.poll() is None  # not actually killed (os.kill was a no-op)
    finally:
        holder.kill()
