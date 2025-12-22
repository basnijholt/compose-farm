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

Type "# Generate example stacks with cf config example"
Enter
Sleep 500ms

Type "# List available templates"
Enter
Sleep 500ms

Type "cf config example --list"
Enter
Sleep 3s

Type "# Create a temporary directory for demo"
Enter
Sleep 500ms

Type "cd $(mktemp -d)"
Enter
Sleep 500ms

Type "# Generate the full Traefik + whoami setup"
Enter
Sleep 500ms

Type "cf config example full"
Enter
Sleep 3s

Type "# See what was created"
Enter
Sleep 500ms

Type "tree ."
Enter
Sleep 3s

Type "# View the generated config"
Enter
Sleep 500ms

Type "cat compose-farm.yaml"
Enter
Sleep 4s

Type "# View the traefik compose file"
Enter
Sleep 500ms

Type "cat traefik/compose.yaml"
Enter
Sleep 4s

Type "# Ready to deploy! (after editing your domain)"
Enter
Sleep 500ms

Type "cf check --local"
Enter
Sleep 3s
