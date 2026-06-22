import fcntl
import logging
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

import requests
from requests import HTTPError, RequestException

from .ax_utils import check_accessibility_permission, get_focused_app
from .config import Config
from .strategies import extract_context

log = logging.getLogger(__name__)

_BUCKET_PREFIX = "aw-watcher-ax"
_BUCKET_TYPE = "currentwindow"
_PERMISSION_RETRY_SEC = 30
_HOSTNAME = socket.gethostname()  # constant for the process; the bucket id derives from it
_LOCK_PATH = Path.home() / "Library" / "Caches" / "aw-watcher-ax" / "watcher.lock"


def _acquire_single_instance_lock(lock_path: Path | None = None) -> IO[str] | None:
    """Take an exclusive flock so only one daemon writes to the bucket.

    A duplicate instance (e.g. the .app double-launched alongside the launchd
    agent) would post a second, independent heartbeat series to the same
    bucket — and a long-lived copy keeps running stale in-memory code after the
    venv is updated, silently corrupting the data. flock is released by the
    kernel when the holder dies (even on SIGKILL), so a crashed daemon never
    leaves a stale lock. Returns the open handle (keep it alive for the process
    lifetime) on success, or None if another instance already holds the lock.
    """
    # Resolve _LOCK_PATH at call time (not as a default arg) so tests can
    # redirect it and the daemon picks up the module-level value.
    if lock_path is None:
        lock_path = _LOCK_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _bucket_id() -> str:
    return f"{_BUCKET_PREFIX}_{_HOSTNAME}"


def _ensure_bucket(base_url: str, bucket_id: str) -> None:
    url = f"{base_url}/api/0/buckets/{bucket_id}"
    payload = {
        "client": "aw-watcher-ax",
        "type": _BUCKET_TYPE,
        "hostname": _HOSTNAME,
    }
    resp = requests.post(url, json=payload, timeout=10)
    # 200/201 = created, 304 = already exists (AW returns this for idempotency)
    if resp.status_code in (200, 201, 304):
        return
    resp.raise_for_status()


def _ensure_bucket_with_retry(
    base_url: str, bucket_id: str, *, max_attempts: int | None = None
) -> None:
    """Call _ensure_bucket with exponential backoff.

    max_attempts=None retries forever (daemon mode). Otherwise gives up
    after that many attempts and re-raises the last exception.
    """
    attempt = 0
    backoff = 1.0
    while True:
        attempt += 1
        try:
            _ensure_bucket(base_url, bucket_id)
            return
        except RequestException as e:
            if max_attempts is not None and attempt >= max_attempts:
                raise
            log.warning(
                "bucket create failed (attempt %d): %s; retrying in %.0fs",
                attempt,
                e,
                backoff,
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


def _heartbeat(base_url: str, bucket_id: str, data: dict, pulsetime: float) -> None:
    url = f"{base_url}/api/0/buckets/{bucket_id}/heartbeat"
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "duration": 0,
        "data": data,
    }
    resp = requests.post(url, params={"pulsetime": pulsetime}, json=payload, timeout=10)
    resp.raise_for_status()


def _wait_for_permission(once: bool) -> bool:
    if check_accessibility_permission(prompt=True):
        return True
    if once:
        log.error(
            "Accessibility permission not granted. Add this binary under "
            "System Settings → Privacy & Security → Accessibility."
        )
        return False
    log.warning(
        "Accessibility permission not granted. Waiting — grant it in "
        "System Settings → Privacy & Security → Accessibility, then wait."
    )
    while not check_accessibility_permission(prompt=False):
        time.sleep(_PERMISSION_RETRY_SEC)
    log.info("Accessibility permission granted.")
    return True


def run(cfg: Config, *, once: bool = False) -> int:
    # Daemon mode: refuse to start if another instance is already running, so a
    # stray duplicate can't post a competing heartbeat series to the bucket.
    # --once is a transient smoke test and intentionally skips the lock so it
    # can run alongside the daemon. `lock` is bound for the process lifetime.
    if not once:
        lock = _acquire_single_instance_lock()
        if lock is None:
            log.error("another aw-watcher-ax instance is already running; exiting")
            return 5

    if not _wait_for_permission(once):
        return 3

    bucket = _bucket_id()
    _ensure_bucket_with_retry(cfg.aw_base_url, bucket, max_attempts=3 if once else None)
    apps_by_bundle = cfg.apps_by_bundle

    log.info(
        "Watching %d apps: %s",
        len(apps_by_bundle),
        ", ".join(a.name for a in cfg.apps),
    )

    while True:
        if not check_accessibility_permission(prompt=False):
            log.warning("Accessibility permission revoked; waiting for re-grant")
            if not _wait_for_permission(once):
                return 3
        try:
            _poll_once(cfg, bucket, apps_by_bundle)
        except Exception as e:
            if once:
                # --once is a smoke test. Surface the failure (RequestException
                # → exit 4 via cli.main, anything else → traceback + exit 1)
                # instead of returning 0 with only a log line.
                raise
            log.exception("watcher iteration failed: %s", e)
        if once:
            return 0
        time.sleep(cfg.poll_interval_sec)


def _poll_once(cfg: Config, bucket: str, apps_by_bundle: dict) -> None:
    focused = get_focused_app()
    if focused is None:
        return
    pid, bundle_id = focused
    app_cfg = apps_by_bundle.get(bundle_id)
    if app_cfg is None:
        log.debug("skip unmonitored app: %s", bundle_id)
        return
    ctx = extract_context(app_cfg, pid)
    if not ctx:
        log.debug("%s: no context extracted", app_cfg.name)
        return
    data = {"app": app_cfg.name, "context": ctx}
    try:
        _heartbeat(cfg.aw_base_url, bucket, data, pulsetime=cfg.pulsetime_sec)
    except HTTPError as e:
        # A 404 means the bucket vanished (AW datastore reset/migration, or it
        # was deleted). It was created only once at startup, so without this a
        # long-lived daemon would 404 every heartbeat forever and silently
        # record nothing. Recreate it and retry the heartbeat once. Other HTTP
        # errors propagate unchanged.
        if e.response is None or e.response.status_code != 404:
            raise
        log.warning("bucket %s missing (404); recreating", bucket)
        _ensure_bucket(cfg.aw_base_url, bucket)
        _heartbeat(cfg.aw_base_url, bucket, data, pulsetime=cfg.pulsetime_sec)
    log.info("%s: %s", app_cfg.name, ctx)
