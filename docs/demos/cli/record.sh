#!/usr/bin/env bash
# Record all VHS demos
# Run this on a Docker host with compose-farm configured

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(dirname "$SCRIPT_DIR")"
DOCS_DIR="$(dirname "$DEMOS_DIR")"
REPO_DIR="$(dirname "$DOCS_DIR")"
OUTPUT_DIR="$DOCS_DIR/assets"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check for VHS
if ! command -v vhs &> /dev/null; then
    echo "VHS not found. Install with:"
    echo "  brew install vhs"
    echo "  # or"
    echo "  go install github.com/charmbracelet/vhs@latest"
    exit 1
fi

# Ensure output directory exists
mkdir -p "$OUTPUT_DIR"

# Temp output dir (VHS runs from /opt/stacks, so relative paths go here)
TEMP_OUTPUT="/opt/stacks/docs/assets"
mkdir -p "$TEMP_OUTPUT"

# Change to /opt/stacks so cf commands use installed version (not editable install)
cd /opt/stacks

# Ensure compose-farm.yaml has no uncommitted changes (safety check)
if ! git diff --quiet compose-farm.yaml; then
    echo -e "${RED}Error: compose-farm.yaml has uncommitted changes${NC}"
    echo "Commit or stash your changes before recording demos"
    exit 1
fi

echo -e "${BLUE}Recording VHS demos...${NC}"
echo "Output directory: $OUTPUT_DIR"
echo ""

# Function to record a tape
record_tape() {
    local tape=$1
    local name=$(basename "$tape" .tape)
    echo -e "${GREEN}Recording:${NC} $name"
    if vhs "$tape"; then
        echo -e "${GREEN}  ✓ Done${NC}"
    else
        echo -e "${RED}  ✗ Failed${NC}"
        return 1
    fi
}

# Record demos in logical order
echo -e "${YELLOW}=== Phase 1: Basic demos ===${NC}"
record_tape "$SCRIPT_DIR/install.tape"
record_tape "$SCRIPT_DIR/quickstart.tape"
record_tape "$SCRIPT_DIR/logs.tape"

echo -e "${YELLOW}=== Phase 2: Update demo ===${NC}"
record_tape "$SCRIPT_DIR/update.tape"

echo -e "${YELLOW}=== Phase 3: Migration demo ===${NC}"
record_tape "$SCRIPT_DIR/migration.tape"
git -C /opt/stacks checkout compose-farm.yaml  # Reset after migration

echo -e "${YELLOW}=== Phase 4: Apply demo ===${NC}"
record_tape "$SCRIPT_DIR/apply.tape"

# Move GIFs and WebMs from temp location to repo
echo ""
echo -e "${BLUE}Moving recordings to repo...${NC}"
mv "$TEMP_OUTPUT"/*.gif "$OUTPUT_DIR/" 2>/dev/null || true
mv "$TEMP_OUTPUT"/*.webm "$OUTPUT_DIR/" 2>/dev/null || true
rmdir "$TEMP_OUTPUT" 2>/dev/null || true
rmdir "$(dirname "$TEMP_OUTPUT")" 2>/dev/null || true

echo ""
echo -e "${GREEN}Done!${NC} Recordings saved to $OUTPUT_DIR/"
ls -la "$OUTPUT_DIR"/*.gif "$OUTPUT_DIR"/*.webm 2>/dev/null || echo "No recordings found (check for errors above)"
