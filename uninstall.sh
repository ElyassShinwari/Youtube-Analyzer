#!/usr/bin/env bash

GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

echo ""
echo -e "  ${BOLD}YouTube Channel Analyzer — Uninstaller${RESET}"
echo ""

# Stop & disable service
if systemctl --user is-active --quiet yt-analyzer 2>/dev/null; then
    systemctl --user stop    yt-analyzer
    systemctl --user disable yt-analyzer
    echo -e "  ${GREEN}[✓]${RESET} Service stopped and disabled"
fi

rm -f  "$HOME/.config/systemd/user/yt-analyzer.service"
systemctl --user daemon-reload 2>/dev/null || true

rm -rf "$HOME/.local/share/yt-analyzer"
rm -f  "$HOME/.local/bin/yt-analyzer"
rm -f  "$HOME/.local/share/applications/yt-analyzer.desktop"
rm -f  "$HOME/.local/share/icons/hicolor/128x128/apps/yt-analyzer.png"

update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo -e "  ${GREEN}[✓]${RESET} Uninstalled successfully"
echo ""
