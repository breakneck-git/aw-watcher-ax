# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`aw-watcher-ax` is a macOS-only [ActivityWatch](https://activitywatch.net/) watcher that polls the frontmost app and uses the macOS Accessibility API (AX) to extract the active "context" — a conversation title in Claude Desktop, the selected chat in Telegram, the window title in an IDE — and emits it as a heartbeat to a local AW server. It is installed as a launchd agent that runs continuously.

## Common commands

The project uses the in-repo venv created by `install.sh`. Use it directly:

```bash
./install.sh                       # one-shot install: venv + config + launchd agent
.venv/bin/aw-watcher-ax --once     # poll once, emit one heartbeat, exit (smoke test)
.venv/bin/aw-watcher-ax -v         # run with DEBUG logging
.venv/bin/pytest                   # run all tests
.venv/bin/pytest tests/test_strategies.py::test_telegram_picks_selected_row_description
.venv/bin/ruff check               # lint
.venv/bin/ruff format              # format
```

Manage the launchd agent installed by `install.sh`:

```bash
launchctl unload ~/Library/LaunchAgents/com.aw-watcher-ax.plist
launchctl load   ~/Library/LaunchAgents/com.aw-watcher-ax.plist
tail -f ~/Library/Logs/aw-watcher-ax/watcher.log
```

User config lives at `~/.config/aw-watcher-ax/config.toml` (template: `config.toml.example`).

## Architecture

The watcher is a single-threaded poll loop with a deliberately layered separation between AX system calls, app-specific extraction logic, and the AW client. Understanding *why* the layers are split matters more than what each file contains.

### Module layout

- `cli.py` — argparse entry point. `--once` is the smoke-test mode used throughout `test_watcher.py`.
- `config.py` — TOML loader. Validates `strategy` against `VALID_STRATEGIES` and rejects entries missing `bundle_id`/`name`.
- `watcher.py` — the poll loop in `run()`. Ensures the AW bucket exists, then loops `_poll_once` → `sleep(poll_interval_sec)`. Catches and logs every iteration's exceptions so a transient AX failure never kills the daemon.
- `strategies.py` — per-app context extraction. `extract_context()` is the only public entry point.
- `ax_utils.py` — thin wrappers around pyobjc's `ApplicationServices` and `AppKit`.

### Load-time invariants

`load_config` rejects: non-positive `poll_interval_sec` / `pulsetime_sec`; `pulsetime_sec < 2 * poll_interval_sec` (the server-side merge would otherwise fragment); duplicate `bundle_id` in `[[apps]]`; empty `apps` list. These checks encode the expectations in `config.toml.example` — don't silently relax them. Every failure raises `ValueError` and `cli.main` maps that to exit code `2`.

`watcher.run()` returns `int`: `0` normal, `3` permission denied under `--once`. Daemon-mode bucket creation retries forever with exponential backoff capped at 60s; `--once` gives up after 3 attempts and lets the exception bubble so smoke tests fail fast.

### Two layered design rules

**1. `ax_utils.py` lazily imports pyobjc.** Every function does its `from ApplicationServices import ...` inside the function body, raising `RuntimeError` if pyobjc isn't installed. This is what lets `strategies.py` and its full test suite run on Linux: tests in `test_strategies.py` monkey-patch `ax_get`, `ax_walk`, and `create_app_element` on the `strategies` module and feed it `FakeElement` trees, never touching real pyobjc. Preserve this property — don't import pyobjc at module top-level anywhere, and don't add pyobjc calls to `strategies.py` directly.

**2. Heartbeats rely on AW's server-side merge, not client-side state.** `_heartbeat()` posts `duration: 0` events with a `pulsetime` query param. ActivityWatch merges successive events with identical `data` within `pulsetime` seconds into one long event. That's why `pulsetime_sec` must be `>= 2 * poll_interval_sec` (see `config.toml.example`) and why the watcher holds *no* "current event" state between iterations — every poll is a fresh, independent POST. Don't add client-side debouncing or change-detection; it would fight the server.

### Strategy dispatch

`extract_context()` in `strategies.py` resolves a config strategy to an extractor:

- `strategy = "auto"` (default) → look up `BUILTIN[bundle_id]`. If present, use it. Otherwise, try `_extract_heading` then fall back to `_extract_window_title`.
- `strategy = "heading"` → first `AXHeading` in the focused window. Useful for chat-style apps that render the active conversation title as a heading.
- `strategy = "window_title"` → `AXTitle` of the focused window. Useful for IDEs/browsers.

`_clean()` enforces two universal rules: strip empty strings, and drop the value if it equals the app's own name (which is what most apps return when there's no real document/chat focused). Honor both when adding new extractors.

### Adding a built-in extractor for a new app

1. Write `_extract_<app>(app_el, app_name) -> str | None` in `strategies.py`. Use `ax_get` / `ax_walk` from `ax_utils`. Pipe results through `_clean()`.
2. Register it: `BUILTIN["<bundle.id>"] = _extract_<app>`.
3. Add tests in `test_strategies.py` using the `FakeElement` pattern — construct an AX tree of `FakeElement(attrs, children)`, call `_set_app(...)`, then assert on `strategies.extract_context(cfg, pid=0)`. The autouse `_patch_ax` fixture wires the fakes in.

Find an app's bundle id with `osascript -e 'id of app "AppName"'`.

### The `.app` bundle wrapper

`install.sh` does not point launchd at `.venv/bin/aw-watcher-ax` directly. Instead, it copies `app_template/aw-watcher-ax.app/` to `~/Applications/aw-watcher-ax.app`, compiles the tiny `app_template/trampoline.c` into `Contents/MacOS/aw-watcher-ax` with `clang -O2 -Wall`, writes the venv launcher path into `Contents/Resources/launcher-target`, ad-hoc codesigns the bundle (`codesign --force --deep --sign - …`), and points the launchd `ProgramArguments` at the trampoline. The trampoline is a Mach-O binary that reads `launcher-target` at runtime, `fork()`s, and `execv()`s the venv launcher in the child while the parent `waitpid`s.

The reason is TCC: macOS's Accessibility permission tracks a code identity — specifically, the cdhash of the Mach-O process launchd directly spawned. If users grant permission to `python3.11`, the grant breaks the next time Python is upgraded or the venv is rebuilt. Wrapping the launcher in a stable, ad-hoc-signed `.app` bundle is only half the story; the other half is keeping the trampoline *alive* as the parent process. If the trampoline just `execv`'d the venv script, the kernel would walk the shebang chain (`#!python3.11` → Python.app) and replace the process image with Homebrew's Python Mach-O — TCC would then track whichever Python cdhash Homebrew happens to ship, and the grant on our bundle wouldn't apply. A plain bash-script wrapper fails for the same reason (kernel execs `/bin/bash` → execs `python3.11` → process is Python). With the fork+wait trampoline, launchd's direct child stays our Mach-O; python runs as a child and inherits TCC responsibility through the parent chain.

Two invariants make the cdhash stable across reinstalls:
1. `install.sh` compiles the trampoline to the same absolute path (`$APP_BIN`), and ld64 embeds the output filename in the symbol table — different output paths produce different bytes. Don't change the compile target path.
2. ld64's default `LC_UUID` is a content hash, so the UUID — and thus the binary bytes and the cdhash — are deterministic for a fixed source + toolchain. Don't pass `-Wl,-no_uuid`: modern dyld refuses to load Mach-O binaries without an `LC_UUID` load command (`dyld[…]: missing LC_UUID load command` → SIGABRT at startup).

Don't change install.sh to launch the venv binary directly, and don't rewrite the trampoline to plain `execv` — the daemon will appear to start, but on the next Python upgrade or venv rebuild Accessibility will silently fall off and the watcher will collect nothing.

### Permission handling

`_wait_for_permission()` distinguishes `--once` (log + exit) from daemon mode (block forever, re-checking every 30s without re-prompting). macOS updates the AX trust bit live, so the daemon picks up newly-granted permission without a launchctl reload — keep it that way.
