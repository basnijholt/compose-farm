# Compose Demo
# Shows that cf compose passes through ANY docker compose command

Output docs/assets/compose.gif
Output docs/assets/compose.webm

Set Shell "bash"
Set FontSize 14
Set Width 900
Set Height 550
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms

Type "# cf compose runs ANY docker compose command on the right host"
Enter
Sleep 500ms

Type "# See ALL available compose commands"
Enter
Sleep 500ms

Type "cf compose immich --help"
Enter
Sleep 4s

Type "# Show images"
Enter
Sleep 500ms

Type "cf compose immich images"
Enter
Wait+Screen /immich/
Sleep 2s

Type "# Open shell in a container"
Enter
Sleep 500ms

Type "cf compose immich exec immich-machine-learning sh"
Enter
Wait+Screen /#/
Sleep 1s

Type "python3 --version"
Enter
Sleep 1s

Type "exit"
Enter
Sleep 500ms
