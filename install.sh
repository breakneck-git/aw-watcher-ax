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

OS="$(uname -s)"

if [ "$OS" != "Darwin" ]; then
    echo "aw-watcher-ax is macOS-only (uses the Accessibility API). Nothing to install on $OS."
    exit 0
fi

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
rm -rf "$APP_DIR"
cp -R "$APP_TEMPLATE" "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
rm -f "$APP_DIR/Contents/MacOS/.gitkeep" "$APP_DIR/Contents/Resources/.gitkeep"

# Record the venv launcher path for the trampoline to read at runtime.
printf '%s\n' "$VENV_DIR/bin/aw-watcher-ax" > "$APP_TARGET_FILE"

# Compile the Mach-O trampoline. ld64 defaults to a content-hash LC_UUID,
# so identical source + toolchain yield a bit-identical binary across
# reinstalls. That keeps the .app bundle's cdhash stable and preserves
# the user's Accessibility grant. We intentionally do NOT pass -no_uuid:
# modern dyld refuses to load Mach-O binaries missing LC_UUID.
echo "Compiling launcher trampoline..."
clang -O2 -Wall -o "$APP_BIN" "$TRAMPOLINE_SRC"
chmod +x "$APP_BIN"

echo "Ad-hoc codesigning app bundle..."
codesign --force --deep --sign - "$APP_DIR"

LOG_DIR="$HOME/Library/Logs/aw-watcher-ax"
PLIST_SRC="$INSTALL_DIR/com.aw-watcher-ax.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.aw-watcher-ax.plist"

echo "Installing launchd service..."
mkdir -p "$LOG_DIR"
sed -e "s|BIN_PATH|$APP_BIN|g" \
    -e "s|LOG_DIR|$LOG_DIR|g" \
    "$PLIST_SRC" > "$PLIST_DST"

launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo "✓ aw-watcher-ax installed. Polling every 60s."
echo "  App bundle:     $APP_DIR"
echo "  Logs:           $LOG_DIR/watcher.log"
echo ""
echo "  IMPORTANT — Accessibility permission:"
echo "  On first run, macOS will prompt. Grant it to 'aw-watcher-ax' (the .app),"
echo "  NOT to python3.11. Open System Settings → Privacy & Security →"
echo "  Accessibility and make sure 'aw-watcher-ax' is enabled."
echo "  No restart needed after granting — the watcher auto-detects the change."
