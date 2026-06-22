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

### Re-granting after a toolchain update

The Accessibility grant is bound to the bundle's **cdhash** (its ad-hoc code
identity). That cdhash is deterministic for a fixed *source + toolchain*, so it
survives Python upgrades and venv rebuilds — but **a new Xcode / Command Line
Tools version compiles the trampoline to different bytes**, which would change
the cdhash and silently void the grant (the watcher then logs `Accessibility
permission not granted` and collects nothing).

To avoid that, `install.sh` rebuilds the trampoline **only when `trampoline.c`
actually changed** (tracked by content hash in `.venv/.trampoline.sha256`);
otherwise it reuses the existing binary byte-for-byte, keeping the cdhash — and
your grant — stable across reinstalls and toolchain updates. If `trampoline.c`
*does* change, the cdhash necessarily changes and `install.sh` prints a warning
telling you to re-enable `aw-watcher-ax` in System Settings (toggle off/on, or
remove and re-add). A `tccutil reset Accessibility com.aw-watcher-ax` followed
by a reload gives the cleanest re-prompt.

#### Never re-grant again (recommended): sign with a stable certificate

For a grant that survives **everything** — toolchain bumps *and* `trampoline.c`
edits — sign the bundle with a stable self-signed code-signing certificate
instead of ad-hoc. TCC then keys the grant on the certificate-based *designated
requirement* (`identifier "com.aw-watcher-ax" and certificate leaf = H"…"`),
which any future build signed with the same cert satisfies regardless of cdhash.

```bash
./scripts/create-signing-cert.sh   # one-time: makes a ~50y self-signed identity
./install.sh                        # now signs with it automatically
```

`install.sh` uses the identity if present and falls back to ad-hoc otherwise.
After the first cert-signed install the code identity changes once, so re-grant
Accessibility one final time (`install.sh` warns you) — it then sticks for good.
The cert is self-signed and untrusted, which is fine: `codesign` signs with it
anyway and TCC matches the certificate leaf, not a trust chain. If the login
keychain is wiped, re-run the script and re-grant once.

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
