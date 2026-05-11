#!/usr/bin/env bash
# Shotsmith installer for macOS / Linux
# Copies Shotsmith.py to Resolve's Scripts/Utility folder

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/Shotsmith.py"

case "$(uname)" in
    Darwin)
        DEST="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
        ;;
    Linux)
        DEST="/opt/resolve/Fusion/Scripts/Utility"
        ;;
    *)
        echo "Unsupported OS: $(uname)"
        exit 1
        ;;
esac

echo
echo "Shotsmith installer"
echo "-------------------"
echo "Source:      $SRC"
echo "Destination: $DEST"
echo

if [ ! -f "$SRC" ]; then
    echo "[ERROR] Shotsmith.py not found next to this installer."
    exit 1
fi

if [ ! -d "$DEST" ]; then
    echo "[ERROR] DaVinci Resolve's Scripts folder not found at:"
    echo "        $DEST"
    echo "        Is Resolve installed?"
    exit 1
fi

# May require sudo on macOS for /Library writes
if ! cp "$SRC" "$DEST/" 2>/dev/null; then
    echo "Need elevated permissions to write to $DEST — re-running with sudo…"
    sudo cp "$SRC" "$DEST/"
fi

echo "[OK] Shotsmith installed."
echo
echo "Next steps:"
echo "  1. In Resolve: Preferences > System > General"
echo "     set 'External scripting using' to Local, then restart Resolve."
echo "  2. Open a project and timeline."
echo "  3. Workspace > Scripts > Utility > Shotsmith"
echo
echo "PySide6 will auto-install on first run."
echo
