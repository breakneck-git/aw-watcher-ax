import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "aw-watcher-ax" / "config.toml"

VALID_STRATEGIES = ("auto", "window_title", "heading")


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

    poll_interval_sec = int(data.get("poll_interval_sec", 60))
    pulsetime_sec = int(data.get("pulsetime_sec", 180))
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

    return Config(
        poll_interval_sec=poll_interval_sec,
        pulsetime_sec=pulsetime_sec,
        aw_base_url=data.get("aw_base_url", "http://localhost:5600"),
        apps=apps,
    )
