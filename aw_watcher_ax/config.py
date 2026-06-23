import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "aw-watcher-ax" / "config.toml"

VALID_STRATEGIES = ("auto", "window_title", "heading")


def _require_int_seconds(data: dict, key: str, default: int) -> int:
    """Read an integer-seconds field, rejecting non-int types.

    bool is an int subclass and TOML floats/strings/arrays are distinct types;
    int() would silently truncate a float or raise an uncaught TypeError on a
    list. Reject anything that isn't a plain int so the failure is a clean
    ValueError (exit code 2) naming the field, not a stdlib traceback.
    """
    raw = data.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{key} must be an integer number of seconds, got {raw!r}")
    return raw


@dataclass
class AppConfig:
    bundle_id: str
    name: str
    strategy: str = "auto"


@dataclass
class Config:
    poll_interval_sec: int = 60
    pulsetime_sec: int = 180
    aw_base_url: str = "http://localhost:5600"
    apps: list[AppConfig] = field(default_factory=list)

    @property
    def apps_by_bundle(self) -> dict[str, AppConfig]:
        return {a.bundle_id: a for a in self.apps}


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise FileNotFoundError(
            f"Config not found: {path}\nCopy config.toml.example to that path and edit as needed."
        )
    with open(path, "rb") as f:
        data = tomllib.load(f)

    apps: list[AppConfig] = []
    seen: set[str] = set()
    seen_names: set[str] = set()
    for entry in data.get("apps", []):
        if "bundle_id" not in entry or "name" not in entry:
            raise ValueError(f"[[apps]] entry missing bundle_id or name: {entry}")
        bundle_id = entry["bundle_id"]
        name = entry["name"]
        if not isinstance(bundle_id, str) or not bundle_id.strip():
            raise ValueError(f"[[apps]] entry has empty or invalid bundle_id: {entry}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"[[apps]] entry has empty or invalid name: {entry}")
        if bundle_id in seen:
            raise ValueError(f"duplicate bundle_id in [[apps]]: {bundle_id!r}")
        seen.add(bundle_id)
        # Distinct bundle_ids sharing a name is allowed (mapping app variants to
        # one logical name), but AW keys events on the name, so warn — it is
        # usually an accident that makes per-app data ambiguous.
        if name in seen_names:
            log.warning(
                "duplicate app name %r across multiple bundle_ids; "
                "their ActivityWatch events will be indistinguishable",
                name,
            )
        seen_names.add(name)
        strategy = entry.get("strategy", "auto")
        if strategy not in VALID_STRATEGIES:
            raise ValueError(
                f"invalid strategy {strategy!r} for {bundle_id!r}. "
                f"Must be one of {VALID_STRATEGIES}"
            )
        apps.append(
            AppConfig(
                bundle_id=bundle_id,
                name=name,
                strategy=strategy,
            )
        )

    if not apps:
        raise ValueError("config must declare at least one [[apps]] entry")

    poll_interval_sec = _require_int_seconds(data, "poll_interval_sec", 60)
    pulsetime_sec = _require_int_seconds(data, "pulsetime_sec", 180)
    if poll_interval_sec <= 0:
        raise ValueError(f"poll_interval_sec must be positive, got {poll_interval_sec}")
    if pulsetime_sec <= 0:
        raise ValueError(f"pulsetime_sec must be positive, got {pulsetime_sec}")
    if pulsetime_sec < 2 * poll_interval_sec:
        raise ValueError(
            f"pulsetime_sec ({pulsetime_sec}) must be >= 2 * poll_interval_sec "
            f"({2 * poll_interval_sec}); otherwise ActivityWatch will not merge "
            f"successive heartbeats into a single event"
        )

    aw_base_url = data.get("aw_base_url", "http://localhost:5600")
    if not isinstance(aw_base_url, str):
        raise ValueError(f"aw_base_url must be a string, got {aw_base_url!r}")
    # urlparse silently ignores surrounding whitespace, so a value like
    # "http://host:5600 " would validate but then build "http://host:5600 /api/...".
    # Strip before validating AND storing so the two always agree.
    aw_base_url = aw_base_url.strip()
    parsed = urlparse(aw_base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(
            f"aw_base_url must be an http:// or https:// URL with a host, got {aw_base_url!r}"
        )
    # Strip any trailing slash so URL building (f"{base}/api/0/...") never emits
    # a double slash, which some servers route differently or 404.
    aw_base_url = aw_base_url.rstrip("/")

    return Config(
        poll_interval_sec=poll_interval_sec,
        pulsetime_sec=pulsetime_sec,
        aw_base_url=aw_base_url,
        apps=apps,
    )
