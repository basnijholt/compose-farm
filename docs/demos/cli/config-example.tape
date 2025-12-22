# Config Example Demo
# Shows cf config example command

Output docs/assets/config-example.gif
Output docs/assets/config-example.webm

Set Shell "bash"
Set FontSize 14
Set Width 900
Set Height 600
Set Theme "Catppuccin Mocha"
Set FontFamily "FiraCode Nerd Font"
Set TypingSpeed 50ms
Env BAT_PAGING "always"

Type "# Generate example stacks with cf config example"
Enter
Sleep 500ms

Type "# List available templates"
Enter
Sleep 500ms

Type "cf config example --list"
Enter
Wait+Screen /Usage:/
Sleep 2s

Type "# Create a directory for our stacks"
Enter
Sleep 500ms

Type "mkdir -p ~/compose && cd ~/compose"
Enter
Wait
Sleep 500ms

Type "# Generate the full Traefik + whoami setup"
Enter
Sleep 500ms

Type "cf config example full"
Enter
Wait+Screen /Example 'full' created/
Sleep 2s

Type "# See what was created"
Enter
Sleep 500ms

Type "tree ."
Enter
Wait
Sleep 2s

Type "# View the generated config"
Enter
Sleep 500ms

Type "bat compose-farm.yaml"
Enter
Sleep 3s
Type "q"
Sleep 500ms

Type "# View the traefik compose file"
Enter
Sleep 500ms

Type "bat traefik/compose.yaml"
Enter
Sleep 3s
Type "q"
Sleep 500ms

Type "# Validate the config"
Enter
Sleep 500ms

Type "cf check --local"
Enter
Wait
Sleep 2s

Type "# Create the Docker network"
Enter
Sleep 500ms

Type "cf init-network"
Enter
Wait
Sleep 1s

Type "# Deploy everything!"
Enter
Sleep 500ms

Type "cf up --all"
Enter
Wait
Sleep 3s

Type "# Verify it's running"
Enter
Sleep 500ms

Type "cf ps"
Enter
Wait
Sleep 2s
