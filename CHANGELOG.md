# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `ax_utils.ax_set`: wrapper around `AXUIElementSetAttributeValue`.
- Built-in Claude Desktop extractor that finds the chat title via the `Session options` popup anchor in Claude's Electron AX tree.
- `extract_context` now flips `AXManualAccessibility=True` on the app element before walking the tree, forcing Electron/Chromium apps to populate their accessibility tree on demand. No-op on native apps.

### Removed
- Telegram built-in extractor and the default `[[apps]]` entry in `config.toml.example`. The current `ru.keepcoder.Telegram` client renders its chat UI without exposing any AX subtree (the window has zero accessible children), so no heuristic can recover the active chat title.

### Fixed
- Regression from 0.2.0: removing the old `_extract_claude` left Claude Desktop with no working extractor, because Electron's AX tree is empty until `AXManualAccessibility` is set and the generic `heading`/`window_title` fallback returned nothing useful.

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
