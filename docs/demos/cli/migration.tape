# Migration Demo
# Shows automatic stack migration when host changes

Output docs/assets/migration.gif
Output docs/assets/migration.webm

Set Shell "bash"
Set FontSize 14
Set Width 1000
Set Height 600
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms

Type "# Current status: audiobookshelf on 'nas'"
Enter
Sleep 500ms

Type "cf ps audiobookshelf"
Enter
Wait+Screen /PORTS/

Type "# Edit config to move it to 'anton'"
Enter
Sleep 1s

Type "nvim /opt/stacks/compose-farm.yaml"
Enter
Wait+Screen /stacks:/

# Search for audiobookshelf
Type "/audiobookshelf"
Enter
Sleep 1s

# Move to the host value (nas) and change it
Type "f:"
Sleep 500ms
Type "w"
Sleep 500ms
Type "ciw"
Sleep 500ms
Type "anton"
Escape
Sleep 1s

# Save and quit
Type ":wq"
Enter
Sleep 1s

Type "# Run up - automatically migrates!"
Enter
Sleep 500ms

Type "cf up audiobookshelf"
Enter
# Wait for migration phases: first the stop on old host
Wait+Screen /Migrating|down/
# Then wait for start on new host
Wait+Screen /Starting|up/
# Finally wait for completion
Wait

Type "# Verify: audiobookshelf now on 'anton'"
Enter
Sleep 500ms

Type "cf ps audiobookshelf"
Enter
Wait+Screen /PORTS/
Sleep 3s
