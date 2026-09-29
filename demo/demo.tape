# Render with: vhs demo/demo.tape  (run from the repo root with songforge on PATH)
Output demo/cli.gif

Set Shell "zsh"
Set FontSize 15
Set Width 1100
Set Height 680
Set Padding 18
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms

Hide
Type "export PATH=$PWD/.venv/bin:$PATH && clear"
Enter
Show

Type "songforge doctor"
Sleep 400ms
Enter
Sleep 7s

Type "songforge list"
Sleep 400ms
Enter
Sleep 5s

Type "songforge generate --help | head -20"
Sleep 400ms
Enter
Sleep 4s
