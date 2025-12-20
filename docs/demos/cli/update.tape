# Update Demo
# Shows updating stacks (pull + build + down + up)

Output docs/assets/update.gif
Output docs/assets/update.webm

Set Shell "bash"
Set FontSize 14
Set Width 900
Set Height 500
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms

Type "# Update a single stack"
Enter
Sleep 500ms

Type "cf update grocy"
Enter
# Wait for command to complete (chain waits for longer timeout)
Wait+Screen /pull/
Wait+Screen /grocy/
Wait@60s

Type "# Check current status"
Enter
Sleep 500ms

Type "cf ps grocy"
Enter
Wait+Screen /PORTS/
Sleep 1s
