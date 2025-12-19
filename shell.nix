# Development shell with Chromium for browser tests
# Usage: nix-shell --run "uv run pytest tests/web/test_htmx_browser.py -v --no-cov"
{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = [
    pkgs.chromium
  ];

  shellHook = ''
    echo "Chromium available at: $(which chromium)"
    echo ""
    echo "Run browser tests with:"
    echo "  uv run pytest tests/web/test_htmx_browser.py -v --no-cov"
  '';
}
