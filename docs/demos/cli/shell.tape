# Shell Demo
# Shows opening interactive shells in containers

Output docs/assets/shell.gif
Output docs/assets/shell.webm

Set Shell "bash"
Set FontSize 14
Set Width 900
Set Height 550
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms

Type "# Open shell in immich machine-learning container"
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

Type "# Now open shell in the server container"
Enter
Sleep 500ms

Type "cf compose immich exec immich-server sh"
Enter
Wait+Screen /#/
Sleep 1s

Type "ls /usr/src/app"
Enter
Sleep 1s

Type "exit"
Enter
Sleep 500ms
