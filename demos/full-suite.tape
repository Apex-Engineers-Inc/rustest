# VHS tape file for rustest full test suite demo
#
# Run with: vhs demos/full-suite.tape
#
# Shows rustest running a larger test suite with multiple files

Output docs/assets/rustest-full-suite.gif
Output docs/assets/rustest-full-suite.png
Output docs/assets/rustest-full-suite.webm

# Terminal settings
Set FontSize 13
Set Width 1400
Set Height 1000
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms
Set PlaybackSpeed 1.2

# Show the command
Type "uv run python -m rustest tests/"
Sleep 500ms
Enter

# Let it run (adjust timing based on actual test duration)
Sleep 5s

# Show completion
Sleep 2s
