# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `ax_utils.ax_set`: wrapper around `AXUIElementSetAttributeValue`.
- Built-in Claude Desktop extractor that finds the chat title via the `Session options` popup anchor in Claude's Electron AX tree.
- `extract_context` now flips `AXManualAccessibility=True` on the app element before walking the tree, forcing Electron/Chromium apps to populate their accessibility tree on demand. No-op on native apps.
- `app_template/trampoline.c`: compiled-at-install-time C trampoline that replaces the old bash launcher script inside the `.app` bundle. `install.sh` now requires `clang` (Xcode Command Line Tools).
- Exit code `4` for `RequestException` from the ActivityWatch server (unreachable, timeout, HTTP error). Previously `--once` let the exception bubble, producing a Python traceback and undocumented exit code `1`.

### Changed
- `.app` bundle's `Contents/MacOS/aw-watcher-ax` is now a Mach-O trampoline compiled from `trampoline.c`, not a bash shim. It reads the venv launcher path from `Contents/Resources/launcher-target`, forks, and execs the venv launcher in the child while the parent waits.

### Removed
- Telegram built-in extractor and the default `[[apps]]` entry in `config.toml.example`. The current `ru.keepcoder.Telegram` client renders its chat UI without exposing any AX subtree (the window has zero accessible children), so no heuristic can recover the active chat title.

### Fixed
- `app_template/trampoline.c` now installs `sigaction` handlers for `SIGTERM`/`SIGINT` that forward the signal to the Python child. Previously `launchctl unload` sent SIGTERM to the trampoline (waitpid got `EINTR`) but left the child untouched, so Python kept running until launchd's grace period elapsed and SIGKILL arrived — no chance for graceful shutdown.
- Watcher re-checks Accessibility permission at the top of every poll iteration. If the bit drops at runtime (user revoked the grant in System Settings), the daemon now logs a warning and re-enters `_wait_for_permission` instead of silently extracting nothing forever. Re-grant also now triggers automatic recovery without a launchctl reload.
- `config.py` rejects empty or non-string `bundle_id` and `name` in `[[apps]]` entries. Previously `name = ""` silently flowed into the `data.app` field of every heartbeat and broke downstream matching (e.g. aw-notion note enrichment, which joins ax events to window events by app name).
- `config.py` rejects `aw_base_url` values that don't start with `http://` or `https://`. Previously a typo or non-URL value silently flowed through to `requests.post()` and surfaced as a confusing `RequestException` at runtime.
- Regression from 0.2.0: removing the old `_extract_claude` left Claude Desktop with no working extractor, because Electron's AX tree is empty until `AXManualAccessibility` is set and the generic `heading`/`window_title` fallback returned nothing useful.
- Launchd daemon Accessibility grant was silently ineffective because the bash launcher triggered the kernel's shebang chain (`/bin/bash` → `python3.11`), so TCC ended up tracking the Homebrew Python Mach-O's cdhash instead of the `.app` bundle's. The fork+wait C trampoline keeps our Mach-O alive as launchd's direct child, so python inherits TCC responsibility through the parent chain.
- `get_focused_app()` returned a stale value in the long-running daemon: `NSWorkspace.frontmostApplication` is driven by window-server distributed notifications, which only get delivered when the current thread's runloop runs. Without a runloop the value latched to whatever was frontmost at daemon start and every subsequent poll returned the same app regardless of actual focus changes. Pumping the runloop for 20 ms before each query drains pending notifications so the result is fresh.

## [0.2.0] - 2026-04-13

### Added
- Config validation: positive `poll_interval_sec` / `pulsetime_sec`, minimum ratio between them, duplicate `bundle_id` detection, non-empty apps list.
- CLI: `--version` flag, exit codes (`2` for config errors, `3` for denied Accessibility permission under `--once`).
- Watcher: exponential-backoff retry around the initial bucket-create request.
- `.app` bundle wrapper installed into `~/Applications/aw-watcher-ax.app`, ad-hoc codesigned by `install.sh`, so macOS TCC tracks a stable identity instead of a versioned Python interpreter.
- `LICENSE`, `CHANGELOG.md`.

### Removed
- Dead `_extract_claude` dispatcher (the `auto` default path already handles Claude Desktop).

## [0.1.0] - 2026-04-13

### Added
- Initial release: launchd-managed macOS watcher, per-app strategy dispatch (`auto`/`heading`/`window_title`), built-in Telegram extractor, AW heartbeat with server-side `pulsetime` merging.
