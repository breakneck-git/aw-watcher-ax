# aw-watcher-ax

[ActivityWatch](https://activitywatch.net/) watcher for macOS. Polls the focused app and uses the Accessibility API to capture the active *context* — the conversation title in Claude Desktop, the window title in your IDE — and emits it as a heartbeat to your local AW server.

Built-in extractor for Claude Desktop. Other apps work via the default `auto` strategy, which falls back through `heading` → `window_title`.

## Install

Requires Python 3.11+ and Xcode Command Line Tools (for `clang`) on macOS.

```bash
./install.sh
```

This will:

1. Create a venv in `.venv/` and install the package into it.
2. Copy the `.app` bundle template to `~/Applications/aw-watcher-ax.app`, compile a small C trampoline as `Contents/MacOS/aw-watcher-ax`, record the venv launcher path in `Contents/Resources/launcher-target`, and ad-hoc codesign the bundle.
3. Drop `config.toml` at `~/.config/aw-watcher-ax/config.toml` from the template.
4. Install and load a launchd agent at `~/Library/LaunchAgents/com.aw-watcher-ax.plist`.

## Grant Accessibility permission

On first run, macOS will pop up a permission prompt. **Grant it to `aw-watcher-ax` (the .app), not to `python3.11`.**

If you don't see the prompt, open it manually:

> System Settings → Privacy & Security → Accessibility → enable `aw-watcher-ax`

The watcher exists as a real `.app` bundle precisely so macOS TCC tracks a stable bundle identity. If you grant the permission to a bare `python3.11` binary, it will silently break the next time you upgrade Python or rebuild the venv.

No restart needed after granting — the watcher polls the trust bit and resumes automatically.

## Configure

Edit `~/.config/aw-watcher-ax/config.toml`. See `config.toml.example` for the full schema and defaults.

To find an app's bundle id:

```bash
osascript -e 'id of app "AppName"'
```

## Logs and control

```bash
tail -f ~/Library/Logs/aw-watcher-ax/watcher.log

launchctl unload ~/Library/LaunchAgents/com.aw-watcher-ax.plist
launchctl load   ~/Library/LaunchAgents/com.aw-watcher-ax.plist
```

For a one-shot smoke test (poll once, emit one heartbeat, exit):

```bash
.venv/bin/aw-watcher-ax --once -v
```
