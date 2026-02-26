#!/usr/bin/env bash
# Launcher for YouTube Channel Analyzer — opens as a native desktop window
APP_DIR="$HOME/.local/share/yt-analyzer"
exec "$APP_DIR/.venv/bin/python" "$APP_DIR/desktop.py"
