# Apply Demo
# Shows cf apply previewing and reconciling state

Output docs/assets/apply.gif
Output docs/assets/apply.webm

Set Shell "bash"
Set FontSize 14
Set Width 900
Set Height 600
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms

Type "# Preview what would change"
Enter
Sleep 500ms

Type "cf apply --dry-run"
Enter
Wait

Type "# Check current status"
Enter
Sleep 500ms

Type "cf stats"
Enter
Wait+Screen /Summary/
Sleep 2s

Type "# Apply the changes"
Enter
Sleep 500ms

Type "cf apply"
Enter
# Wait for shell prompt (command complete)
Wait
Sleep 4s
