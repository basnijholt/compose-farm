# Installation Demo
# Shows installing compose-farm with uv

Output docs/assets/install.gif
Output docs/assets/install.webm

Set Shell "bash"
Set FontSize 14
Set Width 900
Set Height 600
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms
Env FORCE_COLOR "1"

Hide
Type "export PATH=$HOME/.local/bin:$PATH && uv tool uninstall compose-farm 2>/dev/null; clear"
Enter
Show
Type "# Install with uv (recommended)"
Enter
Sleep 500ms

Type "uv tool install compose-farm"
Enter
Wait+Screen /Installed|already installed/

Type "# Verify installation"
Enter
Sleep 500ms

Type "cf --version"
Enter
Wait+Screen /compose-farm/
Sleep 1s

Type "cf --help | less"
Enter
Sleep 2s
PageDown
Sleep 2s
Type "q"
Sleep 2s
