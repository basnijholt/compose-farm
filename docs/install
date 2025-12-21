#!/bin/sh
# Compose Farm bootstrap script
# Usage: curl -fsSL https://compose-farm.nijho.lt/install | sh
#
# This script installs uv (if needed) and then installs compose-farm as a uv tool.

set -e

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is not installed. Installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    echo "uv installation complete!"
    echo ""

    if [ -x ~/.local/bin/uv ]; then
        ~/.local/bin/uv tool install compose-farm
    else
        echo "Please restart your shell and run this script again"
        echo ""
        exit 0
    fi
else
    uv tool install compose-farm
fi

echo ""
echo "compose-farm is installed!"
echo "Run 'cf --help' to get started."
echo "If 'cf' is not found, restart your shell or run: source ~/.bashrc"
