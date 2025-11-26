# VHS tape file for rustest basic output demo
#
# Run with: vhs demos/basic-output.tape
#
# This generates demonstration assets for documentation

Output docs/assets/rustest-output.gif
Output docs/assets/rustest-output.png
Output docs/assets/rustest-output.webm

# Terminal settings
Set FontSize 14
Set Width 1400
Set Height 800
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms
Set PlaybackSpeed 1.0

# Show the command
Type "uv run python -m rustest examples/tests/"
Sleep 500ms
Enter

# Let it run and complete
Sleep 3s

# Show completion
Sleep 1s
