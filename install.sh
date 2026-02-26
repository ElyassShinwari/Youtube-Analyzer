#!/usr/bin/env bash
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}${BOLD}  [•]${RESET} $*"; }
success() { echo -e "${GREEN}${BOLD}  [✓]${RESET} $*"; }
warn()    { echo -e "${YELLOW}${BOLD}  [!]${RESET} $*"; }
error()   { echo -e "${RED}${BOLD}  [✗]${RESET} $*"; exit 1; }

INSTALL_DIR="$HOME/.local/share/yt-analyzer"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/128x128/apps"
PORT=5731

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo -e "  ${RED}${BOLD}▶  YouTube Channel Analyzer — Installer${RESET}"
echo ""

# ── preflight checks ──────────────────────────────────────────────────────────
command -v python3 &>/dev/null          || error "python3 not found."
python3 -c "import venv" 2>/dev/null    || error "python3-venv missing. Run: sudo apt install python3-venv"
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python $PY_VER detected"

# ── stop existing service (if any) ───────────────────────────────────────────
if systemctl --user is-active --quiet yt-analyzer 2>/dev/null; then
    info "Stopping existing service..."
    systemctl --user stop yt-analyzer
fi

# ── copy app files ────────────────────────────────────────────────────────────
info "Copying files to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/analyzer.py"      "$INSTALL_DIR/"
cp "$SCRIPT_DIR/app.py"           "$INSTALL_DIR/"
cp "$SCRIPT_DIR/desktop.py"       "$INSTALL_DIR/"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/launch.sh"        "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/templates"     "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/launch.sh"
success "Files copied"

# ── API key / .env ────────────────────────────────────────────────────────────
if [ -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env" "$INSTALL_DIR/.env"
    success "API key copied from .env"
elif [ -f "$INSTALL_DIR/.env" ] && grep -q "YOUTUBE_API_KEY=." "$INSTALL_DIR/.env"; then
    success "Existing API key kept"
else
    echo ""
    echo -e "  ${YELLOW}A YouTube Data API v3 key is required.${RESET}"
    echo -e "  ${CYAN}https://console.cloud.google.com${RESET} → Enable 'YouTube Data API v3' → Credentials → Create API Key"
    echo ""
    read -rp "  Paste your API key (or Enter to skip): " API_KEY
    if [ -n "$API_KEY" ]; then
        echo "YOUTUBE_API_KEY=$API_KEY" > "$INSTALL_DIR/.env"
        success "API key saved"
    else
        warn "No API key set — edit $INSTALL_DIR/.env before running"
        echo "YOUTUBE_API_KEY=" > "$INSTALL_DIR/.env"
    fi
fi

# ── virtual environment ───────────────────────────────────────────────────────
info "Setting up Python environment ..."
python3 -m venv "$INSTALL_DIR/.venv" --system-site-packages
"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
success "Dependencies installed"

# ── icon ──────────────────────────────────────────────────────────────────────
mkdir -p "$ICON_DIR"
cp "$SCRIPT_DIR/icon.png" "$ICON_DIR/yt-analyzer.png"
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
success "Icon installed"

success "Launcher configured (opens as native window)"

# ── desktop entry ─────────────────────────────────────────────────────────────
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/yt-analyzer.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=YouTube Analyzer
GenericName=YouTube Channel Analytics
Comment=Analyze YouTube channel statistics, trends and top videos
Exec=$INSTALL_DIR/launch.sh
Icon=$ICON_DIR/yt-analyzer.png
Terminal=false
Categories=Network;Utility;
Keywords=youtube;analytics;channel;video;statistics;
StartupNotify=true
EOF
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
success "App entry added to launcher"

# ── CLI shortcut ──────────────────────────────────────────────────────────────
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/yt-analyzer" <<EOF
#!/usr/bin/env bash
exec $INSTALL_DIR/launch.sh
EOF
chmod +x "$BIN_DIR/yt-analyzer"
success "CLI command: yt-analyzer"

# ── PATH warning ──────────────────────────────────────────────────────────────
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    warn "$BIN_DIR is not in PATH — add to ~/.bashrc:"
    echo -e "  ${CYAN}export PATH=\"\$HOME/.local/bin:\$PATH\"${RESET}"
fi

# ── done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "  ${GREEN}${BOLD}Installation complete!${RESET}"
echo ""
echo -e "  ${BOLD}Terminal command:${RESET}  ${CYAN}yt-analyzer${RESET}"
echo -e "  ${BOLD}App launcher    :${RESET}  Search ${CYAN}YouTube Analyzer${RESET} in Activities"
echo ""
