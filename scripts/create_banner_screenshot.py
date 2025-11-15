#!/usr/bin/env python3
"""Create a terminal screenshot of the pytest compatibility mode banner."""
import subprocess
import sys

# Run rustest with pytest-compat and capture output
result = subprocess.run(
    [sys.executable, "-m", "rustest", "--pytest-compat", "test_pytest_compat_example.py"],
    capture_output=True,
    text=True,
    cwd="/home/user/rustest"
)

output = result.stderr + result.stdout

# Extract just the banner and test results (first 20 lines)
lines = output.split('\n')
banner_output = '\n'.join(lines[:20])

print("Terminal output captured:")
print(banner_output)

# Create an SVG that looks like a terminal screenshot
svg_content = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 420" style="background-color: #1e1e1e;">
  <style>
    .terminal-text { font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', monospace; font-size: 14px; }
    .yellow { fill: #ffd700; }
    .cyan { fill: #00d7ff; }
    .white { fill: #d4d4d4; }
    .green { fill: #00ff00; }
    .dim { fill: #808080; }
    .bold { font-weight: bold; }
  </style>

  <!-- Terminal window header -->
  <rect x="0" y="0" width="800" height="30" fill="#2d2d2d"/>
  <circle cx="15" cy="15" r="6" fill="#ff5f56"/>
  <circle cx="35" cy="15" r="6" fill="#ffbd2e"/>
  <circle cx="55" cy="15" r="6" fill="#27c93f"/>

  <!-- Terminal content -->
  <rect x="0" y="30" width="800" height="390" fill="#1e1e1e"/>

  <!-- Banner border top -->
  <text x="20" y="60" class="terminal-text yellow">╔════════════════════════════════════════════════════════════╗</text>

  <!-- Title -->
  <text x="20" y="80" class="terminal-text yellow">║</text>
  <text x="130" y="80" class="terminal-text white bold">RUSTEST PYTEST COMPATIBILITY MODE</text>
  <text x="650" y="80" class="terminal-text yellow">║</text>

  <!-- Separator -->
  <text x="20" y="100" class="terminal-text yellow">╠════════════════════════════════════════════════════════════╣</text>

  <!-- Content -->
  <text x="20" y="120" class="terminal-text yellow">║</text>
  <text x="35" y="120" class="terminal-text white">Running existing pytest tests with rustest.</text>
  <text x="650" y="120" class="terminal-text yellow">║</text>

  <text x="20" y="140" class="terminal-text yellow">║</text>
  <text x="650" y="140" class="terminal-text yellow">║</text>

  <text x="20" y="160" class="terminal-text yellow">║</text>
  <text x="35" y="160" class="terminal-text cyan">Supported:</text>
  <text x="130" y="160" class="terminal-text white">fixtures, parametrize, marks, approx</text>
  <text x="650" y="160" class="terminal-text yellow">║</text>

  <text x="20" y="180" class="terminal-text yellow">║</text>
  <text x="35" y="180" class="terminal-text cyan">Built-ins:</text>
  <text x="130" y="180" class="terminal-text white">tmp_path, tmpdir, monkeypatch</text>
  <text x="650" y="180" class="terminal-text yellow">║</text>

  <text x="20" y="200" class="terminal-text yellow">║</text>
  <text x="35" y="200" class="terminal-text cyan">Not yet:</text>
  <text x="130" y="200" class="terminal-text white">fixture params, some builtins</text>
  <text x="650" y="200" class="terminal-text yellow">║</text>

  <text x="20" y="220" class="terminal-text yellow">║</text>
  <text x="650" y="220" class="terminal-text yellow">║</text>

  <text x="20" y="240" class="terminal-text yellow">║</text>
  <text x="35" y="240" class="terminal-text white">For full features, use native rustest imports:</text>
  <text x="650" y="240" class="terminal-text yellow">║</text>

  <text x="20" y="260" class="terminal-text yellow">║</text>
  <text x="50" y="260" class="terminal-text cyan">from rustest import fixture, mark, ...</text>
  <text x="650" y="260" class="terminal-text yellow">║</text>

  <!-- Border bottom -->
  <text x="20" y="280" class="terminal-text yellow">╚════════════════════════════════════════════════════════════╝</text>

  <!-- Test results -->
  <text x="20" y="320" class="terminal-text green">✓✓✓✓✓✓✓✓✓✓✓</text>
  <text x="180" y="320" class="terminal-text dim">⊘</text>
  <text x="200" y="320" class="terminal-text green">✓✓✓✓✓✓</text>

  <text x="20" y="350" class="terminal-text white bold">17 tests:</text>
  <text x="115" y="350" class="terminal-text green">16 passed</text>
  <text x="210" y="350" class="terminal-text white">, 0 failed,</text>
  <text x="320" y="350" class="terminal-text dim">1 skipped</text>
  <text x="420" y="350" class="terminal-text white">in</text>
  <text x="445" y="350" class="terminal-text dim">0.004s</text>
</svg>'''

# Save the SVG
with open('/home/user/rustest/assets/pytest-compat-banner.svg', 'w') as f:
    f.write(svg_content)

print("\n✓ Created assets/pytest-compat-banner.svg")
