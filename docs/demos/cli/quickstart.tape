# Quick Start Demo
# Shows basic cf commands

Output docs/assets/quickstart.gif
Output docs/assets/quickstart.webm

Set Shell "bash"
Set FontSize 14
Set Width 900
Set Height 600
Set Theme "Catppuccin Mocha"
Set FontFamily "FiraCode Nerd Font"
Set TypingSpeed 50ms
Env BAT_PAGING "always"

Type "# Config is just: stack  host"
Enter
Sleep 500ms

Type "# First, define your hosts..."
Enter
Sleep 500ms

Type "bat -r 1:16 compose-farm.yaml"
Enter
Sleep 3s
Type "q"
Sleep 500ms

Type "# Then map each stack to a host"
Enter
Sleep 500ms

Type "bat -r 17:35 compose-farm.yaml"
Enter
Sleep 3s
Type "q"
Sleep 500ms

Type "# Check stack status"
Enter
Sleep 500ms

Type "cf ps immich"
Enter
Wait+Screen /PORTS/

Type "# Start a stack"
Enter
Sleep 500ms

Type "cf up immich"
Enter
Wait

Type "# View logs"
Enter
Sleep 500ms

Type "cf logs immich --tail 5"
Enter
Wait+Screen /immich/
Sleep 2s

Type "#  The magic: move between hosts (nas  anton)"
Enter
Sleep 500ms

Type "# Change host in config (using sed)"
Enter
Sleep 500ms

Type "sed -i 's/audiobookshelf: nas/audiobookshelf: anton/' compose-farm.yaml"
Enter
Sleep 500ms

Type "# Apply changes - auto-migrates!"
Enter
Sleep 500ms

Type "cf apply"
Enter
Sleep 15s

Type "# Verify: now on anton"
Enter
Sleep 500ms

Type "cf ps audiobookshelf"
Enter
Sleep 5s
