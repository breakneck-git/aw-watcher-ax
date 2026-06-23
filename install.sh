#!/bin/bash
set -e

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$INSTALL_DIR/.venv"
CONFIG_DIR="$HOME/.config/aw-watcher-ax"
CONFIG_FILE="$CONFIG_DIR/config.toml"
CONFIG_TEMPLATE="$INSTALL_DIR/config.toml.example"
APP_TEMPLATE="$INSTALL_DIR/app_template/aw-watcher-ax.app"
TRAMPOLINE_SRC="$INSTALL_DIR/app_template/trampoline.c"
APP_DIR="$HOME/Applications/aw-watcher-ax.app"
APP_BIN="$APP_DIR/Contents/MacOS/aw-watcher-ax"
APP_TARGET_FILE="$APP_DIR/Contents/Resources/launcher-target"
LOG_DIR="$HOME/Library/Logs/aw-watcher-ax"
PLIST_SRC="$INSTALL_DIR/com.aw-watcher-ax.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.aw-watcher-ax.plist"

OS="$(uname -s)"

if [ "$OS" != "Darwin" ]; then
    echo "aw-watcher-ax is macOS-only (uses the Accessibility API). Nothing to install on $OS."
    exit 0
fi

# Stop any running instance before we replace the app bundle, so launchd
# doesn't hold a reference to a half-written binary during the copy/compile.
if [ -f "$PLIST_DST" ]; then
    launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

# `launchctl unload` only stops the LaunchAgent. A copy launched outside launchd
# (e.g. the .app double-clicked, registered as application.com.aw-watcher-ax.*)
# keeps running — and because the venv is an editable install, a long-lived
# stray executes stale in-memory code after this reinstall updates the source,
# silently posting a competing heartbeat series to the bucket. That is exactly
# how "0" kept landing in the time log. Kill every instance so only the freshly
# loaded daemon survives. flock alone can't fix this: a pre-update stray holds
# no lock (old code) yet still runs, or holds the lock and blocks the new one.
pkill -f "$APP_BIN" 2>/dev/null || true
pkill -f "$VENV_DIR/bin/aw-watcher-ax" 2>/dev/null || true

PYTHON_BIN=""
for candidate in python3.11 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done
if [ -z "$PYTHON_BIN" ]; then
    echo "Error: Python 3.11+ not found in PATH."
    echo "  brew install python@3.11"
    exit 1
fi

echo "Creating venv and installing aw-watcher-ax (using $PYTHON_BIN)..."
"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -q -e "$INSTALL_DIR"

echo "Creating config directory..."
mkdir -p "$CONFIG_DIR"

if [ ! -f "$CONFIG_FILE" ]; then
    cp "$CONFIG_TEMPLATE" "$CONFIG_FILE"
    echo "⚠️  Config created at $CONFIG_FILE"
    echo "   Edit it to add/remove apps to monitor."
fi

if ! command -v clang >/dev/null 2>&1; then
    echo "Error: clang not found. Install Xcode Command Line Tools:"
    echo "  xcode-select --install"
    exit 1
fi

echo "Installing app bundle to $APP_DIR..."
mkdir -p "$HOME/Applications"

# The Accessibility grant is tied to the bundle's cdhash. ld64 yields a
# bit-identical trampoline only for a fixed source + toolchain, so recompiling
# after an Xcode/CLT update changes the cdhash and silently voids the grant —
# the watcher then sits in "permission denied" collecting nothing. Avoid that:
# rebuild the trampoline ONLY when its source actually changed (content hash,
# not mtime — git checkouts churn mtimes). Otherwise reuse the existing binary
# byte-for-byte, keeping the cdhash — and the grant — stable across reinstalls.
TRAMPOLINE_HASH_FILE="$VENV_DIR/.trampoline.sha256"  # outside the bundle, not codesigned
SRC_HASH="$(shasum -a 256 "$TRAMPOLINE_SRC" | awk '{print $1}')"
# Capture the OLD designated requirement: that is exactly what TCC checks, so a
# re-grant is needed iff it changes (ad-hoc cdhash → different cdhash, or a
# switch between ad-hoc and a signing cert). A cert-signed rebuild keeps the
# same requirement even when the cdhash changes, so the grant survives.
OLD_REQ=""
OLD_CDHASH=""
HAD_BUNDLE=""
STASHED_BIN=""
if [ -x "$APP_BIN" ]; then
    HAD_BUNDLE=1
    # Ad-hoc bundles expose no designated requirement, so also keep the cdhash:
    # for ad-hoc the grant is cdhash-bound, for cert-signed it is requirement-bound.
    OLD_REQ="$(codesign -d --requirements - "$APP_DIR" 2>/dev/null | sed -n 's/^designated => //p')"
    OLD_CDHASH="$(codesign -dvvv "$APP_DIR" 2>&1 | sed -n 's/^CDHash=//p')"
    if [ -f "$TRAMPOLINE_HASH_FILE" ] && [ "$(cat "$TRAMPOLINE_HASH_FILE")" = "$SRC_HASH" ]; then
        STASHED_BIN="$(mktemp)"
        # Clean up the stash even if a later step aborts under `set -e`.
        trap 'rm -f "$STASHED_BIN"' EXIT
        cp "$APP_BIN" "$STASHED_BIN"
    fi
fi

rm -rf "$APP_DIR"
cp -R "$APP_TEMPLATE" "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
rm -f "$APP_DIR/Contents/MacOS/.gitkeep" "$APP_DIR/Contents/Resources/.gitkeep"

# Record the venv launcher path for the trampoline to read at runtime.
printf '%s\n' "$VENV_DIR/bin/aw-watcher-ax" > "$APP_TARGET_FILE"

if [ -n "$STASHED_BIN" ]; then
    echo "Reusing existing trampoline (source unchanged) to preserve the Accessibility grant..."
    cp "$STASHED_BIN" "$APP_BIN"
    rm -f "$STASHED_BIN"
else
    # We intentionally do NOT pass -no_uuid: modern dyld refuses to load a
    # Mach-O missing LC_UUID (SIGABRT at startup).
    echo "Compiling launcher trampoline..."
    clang -O2 -Wall -o "$APP_BIN" "$TRAMPOLINE_SRC"
fi
chmod +x "$APP_BIN"
printf '%s' "$SRC_HASH" > "$TRAMPOLINE_HASH_FILE"

# Prefer a stable self-signed code-signing identity if one is installed: then
# TCC keys the grant on the certificate (designated requirement), so it survives
# every rebuild — toolchain bumps AND trampoline.c edits. Fall back to ad-hoc
# (cdhash-bound grant) when no such identity exists, e.g. a fresh machine.
# Create the identity once; see README "Re-granting after a toolchain update".
SIGN_IDENTITY="aw-watcher-ax Code Signing"
if security find-identity -p codesigning 2>/dev/null | grep -q "$SIGN_IDENTITY"; then
    echo "Codesigning with stable identity '$SIGN_IDENTITY' (grant survives rebuilds)..."
    codesign --force --deep --sign "$SIGN_IDENTITY" "$APP_DIR"
else
    echo "Ad-hoc codesigning app bundle (no stable signing identity found)..."
    codesign --force --deep --sign - "$APP_DIR"
fi

NEW_REQ="$(codesign -d --requirements - "$APP_DIR" 2>/dev/null | sed -n 's/^designated => //p')"
NEW_CDHASH="$(codesign -dvvv "$APP_DIR" 2>&1 | sed -n 's/^CDHash=//p')"
# Re-grant is needed iff the binary no longer satisfies the prior grant: for a
# cert-signed old bundle compare the requirement; for an ad-hoc old bundle (no
# requirement) compare the cdhash.
NEEDS_REGRANT=0
if [ -n "$HAD_BUNDLE" ]; then
    if [ -n "$OLD_REQ" ]; then
        [ "$OLD_REQ" != "$NEW_REQ" ] && NEEDS_REGRANT=1
    else
        [ "$OLD_CDHASH" != "$NEW_CDHASH" ] && NEEDS_REGRANT=1
    fi
fi
if [ "$NEEDS_REGRANT" = 1 ]; then
    echo ""
    echo "⚠️  App code identity changed — the Accessibility grant will NOT carry over."
    echo "    Re-enable 'aw-watcher-ax' in System Settings → Privacy & Security →"
    echo "    Accessibility (toggle off/on, or remove + re-add). The watcher waits"
    echo "    for the grant and resumes automatically once given."
    if printf '%s' "$NEW_REQ" | grep -q "certificate leaf"; then
        echo "    (This is a one-time re-grant: future rebuilds keep this identity.)"
    fi
    echo ""
fi

echo "Installing launchd service..."
mkdir -p "$LOG_DIR"
# Escape sed replacement metacharacters (& \ and the | delimiter) in case $HOME
# contains them, so the paths land verbatim in the generated plist.
esc_sed() { printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'; }
sed -e "s|BIN_PATH|$(esc_sed "$APP_BIN")|g" \
    -e "s|LOG_DIR|$(esc_sed "$LOG_DIR")|g" \
    "$PLIST_SRC" > "$PLIST_DST"

# Under `set -e`, an unguarded `launchctl load` returning nonzero (e.g. the
# service is already bootstrapped) would abort the script at this final step
# after everything is already in place. Surface a clear hint instead.
if ! launchctl load "$PLIST_DST"; then
    echo "Warning: 'launchctl load' returned an error — the service may already be loaded." >&2
    echo "  If the watcher isn't running, reload it with:" >&2
    echo "    launchctl unload \"$PLIST_DST\" && launchctl load \"$PLIST_DST\"" >&2
fi

echo "✓ aw-watcher-ax installed. Polling every 60s."
echo "  App bundle:     $APP_DIR"
echo "  Logs:           $LOG_DIR/watcher.log"
echo ""
echo "  IMPORTANT — Accessibility permission:"
echo "  On first run, macOS will prompt. Grant it to 'aw-watcher-ax' (the .app),"
echo "  NOT to python3.11. Open System Settings → Privacy & Security →"
echo "  Accessibility and make sure 'aw-watcher-ax' is enabled."
echo "  No restart needed after granting — the watcher auto-detects the change."
