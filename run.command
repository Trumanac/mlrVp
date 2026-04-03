#!/usr/bin/env bash
# Double-click this file in macOS Finder to launch mlrVp.
# It creates a virtual environment and installs dependencies automatically.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Check Python is available
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 is not installed or not in PATH."
    echo "Please install Python 3.10+ from https://www.python.org/downloads/"
    read -p "Press Enter to exit..."
    exit 1
fi

if [ -f ".venv/bin/python" ]; then
    .venv/bin/python -m mlrvp
elif [ -f "venv/bin/python" ]; then
    venv/bin/python -m mlrvp
else
    echo "No virtual environment found. Creating one..."
    python3 -m venv .venv
    echo "Installing dependencies..."
    .venv/bin/pip install -r requirements.txt
    echo ""
    echo "Starting mlrVp..."
    .venv/bin/python -m mlrvp
fi
