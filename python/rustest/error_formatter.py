"""Enhanced error formatting for better test failure presentation."""

import re
from typing import Optional, List, Tuple


class Colors:
    """ANSI color codes for terminal output."""
    reset = "\033[0m"
    bold = "\033[1m"
    dim = "\033[2m"
    red = "\033[91m"
    green = "\033[92m"
    yellow = "\033[93m"
    blue = "\033[94m"
    cyan = "\033[96m"
    white = "\033[97m"


class ErrorFormatter:
    """Formats test errors in a user-friendly way."""

    def __init__(self, use_colors: bool = True):
        self.use_colors = use_colors

    def format_failure(self, test_name: str, test_path: str, message: str) -> str:
        """
        Format a test failure message with improved readability.

        Args:
            test_name: Name of the failing test
            test_path: Path to the test file
            message: Raw error message (Python traceback)

        Returns:
            Formatted error message
        """
        lines = []

        # Header with test name and path
        lines.append(f"\n{self._bold(test_name)} {self._dim(f'({test_path})')}")
        lines.append(self._red("─" * 70))

        # Parse the traceback
        parsed = self._parse_traceback(message)

        if parsed:
            # Show the assertion error prominently
            if parsed['error_type']:
                error_header = f"{parsed['error_type']}"
                if parsed['error_message']:
                    error_header += f": {parsed['error_message']}"
                lines.append(f"{self._red('✗')} {self._bold(error_header)}\n")

            # Show the failing code with context
            if parsed['failing_code']:
                lines.append(self._format_code_context(
                    parsed['file_path'],
                    parsed['line_number'],
                    parsed['failing_code'],
                    parsed['context_lines']
                ))

            # Show comparison if available
            if parsed['comparison']:
                lines.append(self._format_comparison(parsed['comparison']))

            # Show simplified stack trace
            if parsed['stack_frames']:
                lines.append(self._format_stack_trace(parsed['stack_frames']))
        else:
            # Fallback: show original message with basic formatting
            lines.append(self._format_raw_error(message))

        return "\n".join(lines)

    def _parse_traceback(self, message: str) -> Optional[dict]:
        """
        Parse a Python traceback to extract useful information.

        Returns a dict with:
        - error_type: Exception class name
        - error_message: Exception message
        - file_path: Path to the file where error occurred
        - line_number: Line number of the error
        - failing_code: The actual line of code that failed
        - context_lines: Lines of code around the failure
        - comparison: Parsed comparison info (for assertions)
        - stack_frames: List of stack frames
        """
        if not message:
            return None

        lines = message.strip().split('\n')

        # Find the exception type and message (usually last line)
        error_type = None
        error_message = None

        for line in reversed(lines):
            # Skip traceback header lines
            if line.strip().startswith('Traceback'):
                continue

            stripped = line.strip()
            if not stripped or stripped.startswith(' '):
                continue

            # Check for exception with message: "ExceptionType: message"
            if ':' in stripped:
                parts = stripped.split(':', 1)
                if parts[0] and not parts[0].startswith(' '):
                    error_type = parts[0].strip()
                    error_message = parts[1].strip() if len(parts) > 1 else None
                    break
            # Check for exception without message: just "ExceptionType"
            elif stripped.endswith('Error') or stripped.endswith('Exception'):
                error_type = stripped
                error_message = None
                break

        # Extract stack frames
        stack_frames = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.strip().startswith('File "'):
                # Parse file path and line number
                match = re.search(r'File "([^"]+)", line (\d+), in (.+)', line)
                if match:
                    file_path = match.group(1)
                    line_num = int(match.group(2))
                    func_name = match.group(3)

                    # Get the code line (next non-empty line)
                    code_line = None
                    j = i + 1
                    while j < len(lines):
                        if lines[j].strip() and not lines[j].strip().startswith('^'):
                            code_line = lines[j]
                            break
                        j += 1

                    stack_frames.append({
                        'file': file_path,
                        'line': line_num,
                        'function': func_name,
                        'code': code_line.strip() if code_line else None
                    })
            i += 1

        # Get the most relevant frame (usually the last user code frame)
        main_frame = None
        for frame in reversed(stack_frames):
            # Skip internal Python/library frames
            if not self._is_internal_frame(frame['file']):
                main_frame = frame
                break

        if not main_frame and stack_frames:
            main_frame = stack_frames[-1]

        # Try to extract comparison info from AssertionError
        comparison = None
        if error_type == 'AssertionError' and main_frame and main_frame['code']:
            comparison = self._parse_assertion(main_frame['code'])

        return {
            'error_type': error_type,
            'error_message': error_message,
            'file_path': main_frame['file'] if main_frame else None,
            'line_number': main_frame['line'] if main_frame else None,
            'failing_code': main_frame['code'] if main_frame else None,
            'context_lines': [],  # Could be enhanced by reading the actual file
            'comparison': comparison,
            'stack_frames': [f for f in stack_frames if not self._is_internal_frame(f['file'])]
        }

    def _is_internal_frame(self, file_path: str) -> bool:
        """Check if a stack frame is from internal Python or test framework code."""
        internal_patterns = [
            '/python3.',
            '/lib/python',
            'importlib',
            '<frozen',
        ]
        return any(pattern in file_path for pattern in internal_patterns)

    def _parse_assertion(self, code: str) -> Optional[dict]:
        """
        Try to parse assertion code to extract expected/actual values.

        Returns dict with 'left', 'operator', 'right' if successful.
        """
        # Handle simple assertions: assert x == y, assert x > y, etc.
        match = re.match(r'assert\s+(.+?)\s*(==|!=|>|<|>=|<=|in|not in|is|is not)\s+(.+?)(?:,|$)', code)
        if match:
            return {
                'left': match.group(1).strip(),
                'operator': match.group(2).strip(),
                'right': match.group(3).strip()
            }
        return None

    def _format_code_context(self, file_path: Optional[str], line_number: Optional[int],
                            failing_code: Optional[str], context_lines: List[str]) -> str:
        """Format the code context around the failure."""
        lines = []

        if file_path and line_number:
            lines.append(f"\n{self._dim('Location:')} {self._cyan(file_path)}:{self._cyan(str(line_number))}")

        if failing_code:
            lines.append(f"\n{self._dim('Code:')}")
            # Show the failing line with an arrow
            lines.append(f"  {self._red('→')} {failing_code.strip()}")

        return "\n".join(lines)

    def _format_comparison(self, comparison: dict) -> str:
        """Format a comparison (expected vs actual) in a readable way."""
        lines = []
        lines.append(f"\n{self._dim('Expression:')} {comparison['left']} {comparison['operator']} {comparison['right']}")

        # Try to make it clear what was compared
        op_explanations = {
            '==': 'should equal',
            '!=': 'should not equal',
            '>': 'should be greater than',
            '<': 'should be less than',
            '>=': 'should be greater than or equal to',
            '<=': 'should be less than or equal to',
            'in': 'should be in',
            'not in': 'should not be in',
            'is': 'should be',
            'is not': 'should not be'
        }

        explanation = op_explanations.get(comparison['operator'], comparison['operator'])
        lines.append(f"  {self._cyan(comparison['left'])} {self._dim(explanation)} {self._cyan(comparison['right'])}")

        return "\n".join(lines)

    def _format_stack_trace(self, stack_frames: List[dict]) -> str:
        """Format a simplified stack trace."""
        if not stack_frames or len(stack_frames) <= 1:
            return ""

        lines = []
        lines.append(f"\n{self._dim('Stack trace:')}")

        for frame in stack_frames:
            location = f"({frame['file']}:{frame['line']})"
            lines.append(f"  {self._dim('at')} {frame['function']} {self._dim(location)}")

        return "\n".join(lines)

    def _format_raw_error(self, message: str) -> str:
        """Format a raw error message when parsing fails."""
        # Just add some basic coloring to the original message
        lines = message.strip().split('\n')
        formatted = []

        for line in lines:
            if line.strip().startswith('File "'):
                formatted.append(self._dim(line))
            elif ':' in line and not line.startswith(' ') and line[0].isupper():
                # Likely the exception line
                parts = line.split(':', 1)
                formatted.append(f"{self._red(parts[0])}: {parts[1] if len(parts) > 1 else ''}")
            elif line.strip().startswith('^'):
                formatted.append(self._red(line))
            else:
                formatted.append(line)

        return "\n".join(formatted)

    # Color helper methods
    def _bold(self, text: str) -> str:
        return f"{Colors.bold}{text}{Colors.reset}" if self.use_colors else text

    def _dim(self, text: str) -> str:
        return f"{Colors.dim}{text}{Colors.reset}" if self.use_colors else text

    def _red(self, text: str) -> str:
        return f"{Colors.red}{text}{Colors.reset}" if self.use_colors else text

    def _green(self, text: str) -> str:
        return f"{Colors.green}{text}{Colors.reset}" if self.use_colors else text

    def _yellow(self, text: str) -> str:
        return f"{Colors.yellow}{text}{Colors.reset}" if self.use_colors else text

    def _cyan(self, text: str) -> str:
        return f"{Colors.cyan}{text}{Colors.reset}" if self.use_colors else text
