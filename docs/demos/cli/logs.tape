# Logs Demo
# Shows viewing stack logs

Output docs/assets/logs.gif
Output docs/assets/logs.webm

Set Shell "bash"
Set FontSize 14
Set Width 900
Set Height 550
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms

Type "# View recent logs"
Enter
Sleep 500ms

Type "cf logs immich --tail 20"
Enter
Wait+Screen /immich/
Sleep 2s
