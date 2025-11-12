"""Enhanced error formatting for better test failure presentation."""

import re
from typing import Optional, List, Tuple, TypedDict


class StackFrame(TypedDict):
    """Type definition for a stack frame."""

    file: str
    line: int
    function: str
    code: Optional[str]


class Comparison(TypedDict):
    """Type definition for parsed comparison."""

    left: str
    operator: str
    right: str


class ParsedTraceback(TypedDict):
    """Type definition for parsed traceback information."""

    error_type: Optional[str]
    error_message: Optional[str]
    file_path: Optional[str]
    line_number: Optional[int]
    failing_code: Optional[str]
    context_lines: List[str]
    comparison: Optional[Comparison]
    stack_frames: List[StackFrame]
    actual_value: Optional[str]
    expected_value: Optional[str]


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
        super().__init__()
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

        # Check if we got any useful data from parsing
        if parsed is not None and (
            parsed["error_type"] or parsed["failing_code"] or parsed["stack_frames"]
        ):
            # Show the assertion error prominently
            if parsed["error_type"]:
                error_header = f"{parsed['error_type']}"
                if parsed["error_message"]:
                    error_header += f": {parsed['error_message']}"
                lines.append(f"{self._red('✗')} {self._bold(error_header)}\n")

            # Show the failing code with context
            if parsed["failing_code"]:
                lines.append(
                    self._format_code_context(
                        parsed["file_path"],
                        parsed["line_number"],
                        parsed["failing_code"],
                        parsed["context_lines"],
                        parsed.get("comparison"),
                        parsed.get("expected_value"),
                        parsed.get("actual_value"),
                    )
                )

            # Show simplified stack trace
            if parsed["stack_frames"]:
                lines.append(self._format_stack_trace(parsed["stack_frames"]))
        else:
            # Fallback: show original message with basic formatting
            lines.append(self._format_raw_error(message))

        return "\n".join(lines)

    def _parse_traceback(self, message: str) -> Optional[ParsedTraceback]:
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
        - actual_value: The actual/received value (if parseable)
        - expected_value: The expected value (if parseable)
        """
        if not message:
            return None

        # Strip out the Rust-injected values marker section from the traceback
        # before parsing (we'll extract it separately)
        traceback_message = message
        if "__RUSTEST_ASSERTION_VALUES__" in message:
            traceback_message = message.split("__RUSTEST_ASSERTION_VALUES__")[0].strip()

        lines = traceback_message.strip().split("\n")

        # Find the exception type and message (usually last line)
        error_type = None
        error_message = None

        for line in reversed(lines):
            # Skip traceback header lines
            if line.strip().startswith("Traceback"):
                continue

            stripped = line.strip()
            if not stripped or stripped.startswith(" "):
                continue

            # Check for exception with message: "ExceptionType: message"
            if ":" in stripped:
                parts = stripped.split(":", 1)
                if parts[0] and not parts[0].startswith(" "):
                    error_type = parts[0].strip()
                    error_message = parts[1].strip() if len(parts) > 1 else None
                    break
            # Check for exception without message: just "ExceptionType"
            elif stripped.endswith("Error") or stripped.endswith("Exception"):
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
                        if lines[j].strip() and not lines[j].strip().startswith("^"):
                            code_line = lines[j]
                            break
                        j += 1

                    stack_frames.append(
                        {
                            "file": file_path,
                            "line": line_num,
                            "function": func_name,
                            "code": code_line.strip() if code_line else None,
                        }
                    )
            i += 1

        # Get the most relevant frame (usually the last user code frame)
        main_frame = None
        for frame in reversed(stack_frames):
            # Skip internal Python/library frames
            if not self._is_internal_frame(frame["file"]):
                main_frame = frame
                break

        if not main_frame and stack_frames:
            main_frame = stack_frames[-1]

        # Try to extract comparison info from AssertionError
        comparison = None
        actual_value = None
        expected_value = None

        if error_type == "AssertionError" and main_frame and main_frame["code"]:
            comparison = self._parse_assertion(main_frame["code"])

            # First, check if Rust injected the actual values (from frame inspection)
            rust_values = self._extract_rust_injected_values(message)
            if rust_values:
                actual_value = rust_values.get("actual")
                expected_value = rust_values.get("expected")
            # Otherwise, try to extract from error message
            elif error_message:
                values = self._extract_values_from_message(error_message, comparison)
                if values:
                    actual_value = values.get("actual")
                    expected_value = values.get("expected")

        return {
            "error_type": error_type,
            "error_message": error_message,
            "file_path": main_frame["file"] if main_frame else None,
            "line_number": main_frame["line"] if main_frame else None,
            "failing_code": main_frame["code"] if main_frame else None,
            "context_lines": [],  # Could be enhanced by reading the actual file
            "comparison": comparison,
            "stack_frames": [f for f in stack_frames if not self._is_internal_frame(f["file"])],
            "actual_value": actual_value,
            "expected_value": expected_value,
        }

    def _is_internal_frame(self, file_path: str) -> bool:
        """Check if a stack frame is from internal Python or test framework code."""
        internal_patterns = [
            "/python3.",
            "/lib/python",
            "importlib",
            "<frozen",
        ]
        return any(pattern in file_path for pattern in internal_patterns)

    def _extract_rust_injected_values(self, message: str) -> Optional[dict[str, str]]:
        """
        Extract values that were injected by the Rust code from frame inspection.

        The Rust code adds a special marker with the actual values.
        Format: __RUSTEST_ASSERTION_VALUES__\nExpected: X\nReceived: Y
        """
        if "__RUSTEST_ASSERTION_VALUES__" not in message:
            return None

        # Split by the marker
        parts = message.split("__RUSTEST_ASSERTION_VALUES__")
        if len(parts) < 2:
            return None

        # Parse the values section
        values_section = parts[1]
        expected = None
        actual = None

        for line in values_section.split("\n"):
            line = line.strip()
            if line.startswith("Expected:"):
                expected = line[9:].strip()  # Remove "Expected:"
            elif line.startswith("Received:"):
                actual = line[9:].strip()  # Remove "Received:"

        if expected is not None and actual is not None:
            return {"expected": expected, "actual": actual}

        return None

    def _extract_values_from_message(
        self, error_message: str, comparison: Optional[Comparison]
    ) -> Optional[dict[str, str]]:
        """
        Try to extract actual and expected values from the error message.

        This works for custom assertion messages that include the values.
        For example: "Expected 5, got 4" or "value should be 10 but was 20"
        """
        if not error_message:
            return None

        # Common patterns in assertion messages
        # Using .+ instead of .+? to be less greedy and capture full values
        patterns = [
            # "Expected X, got Y" - most common pattern
            r"[Ee]xpected\s+(.+?),\s+got\s+(.+?)$",
            # "expected 'X' to be 'Y'"
            r"expected\s+'([^']+)'\s+to\s+be\s+'([^']+)'",
            r'expected\s+"([^"]+)"\s+to\s+be\s+"([^"]+)"',
            # "Expected: X, Received: Y"
            r"[Ee]xpected:\s*(.+?),\s*[Rr]eceived:\s*(.+?)$",
            # "should be X but was Y"
            r"should\s+be\s+at\s+least\s+(\d+)",  # Special case for "at least"
            r"should\s+be\s+(.+?)\s+(?:but\s+was|got)\s+(.+?)$",
            # "X != Y" in message
            r"^(.+?)\s*!=\s*(.+?)$",
            # "got X, expected Y"
            r"got\s+(.+?),\s+expected\s+(.+?)$",
        ]

        for pattern in patterns:
            match = re.search(pattern, error_message, re.IGNORECASE | re.DOTALL)
            if match:
                # Check if it's the "at least" special case
                if "at least" in pattern:
                    # For "Score X should be at least Y", parse differently
                    score_match = re.search(
                        r"(\w+)\s+(\d+)\s+should\s+be\s+at\s+least\s+(\d+)", error_message
                    )
                    if score_match:
                        return {
                            "actual": score_match.group(2),
                            "expected": f"at least {score_match.group(3)}",
                        }
                    return None

                # Determine which group is expected vs actual
                if "got" in pattern.lower() or "received" in pattern.lower():
                    # Pattern has "expected ... got/received"
                    return {
                        "expected": match.group(1).strip().strip("\"'"),
                        "actual": match.group(2).strip().strip("\"'"),
                    }
                elif "to be" in pattern.lower():
                    # "expected X to be Y" - first is actual, second is expected
                    return {
                        "actual": match.group(1).strip().strip("\"'"),
                        "expected": match.group(2).strip().strip("\"'"),
                    }
                elif match.lastindex and match.lastindex >= 2:
                    # Default: try to infer from context
                    g1 = match.group(1).strip().strip("\"'")
                    g2 = match.group(2).strip().strip("\"'")

                    # If the message says "Expected" first, that's probably expected
                    if error_message.lower().startswith("expected"):
                        return {"expected": g1, "actual": g2}
                    else:
                        return {"actual": g1, "expected": g2}

        return None

    def _parse_assertion(self, code: str) -> Optional[Comparison]:
        """
        Try to parse assertion code to extract expected/actual values.

        Returns dict with 'left', 'operator', 'right' if successful.
        """
        # Handle simple assertions: assert x == y, assert x > y, etc.
        match = re.match(
            r"assert\s+(.+?)\s*(==|!=|>|<|>=|<=|in|not in|is|is not)\s+(.+?)(?:,|$)", code
        )
        if match:
            return {
                "left": match.group(1).strip(),
                "operator": match.group(2).strip(),
                "right": match.group(3).strip(),
            }
        return None

    def _format_code_context(
        self,
        file_path: Optional[str],
        line_number: Optional[int],
        failing_code: Optional[str],
        context_lines: List[str],
        comparison: Optional[Comparison] = None,
        expected_value: Optional[str] = None,
        actual_value: Optional[str] = None,
    ) -> str:
        """Format the code context around the failure with pytest-style output."""
        lines = []

        if file_path and line_number:
            # Format as clickable link (path:line format is widely supported)
            location = f"{file_path}:{line_number}"
            # Many terminals and IDEs recognize this format and make it clickable
            lines.append(f"\n {self._dim('─')} {self._cyan(location)}")

        if failing_code:
            lines.append(f"\n{self._dim('Code:')}")

            # Try to read the actual source file for context
            source_context = self._get_source_context(file_path, line_number, num_lines=3)

            if source_context:
                for line_no, code_line in source_context:
                    is_failing_line = line_no == line_number
                    if is_failing_line:
                        # Show the failing line with arrow
                        lines.append(f"  {self._red('→')} {code_line}")
                    else:
                        # Show context line (dimmed)
                        lines.append(f"    {self._dim(code_line)}")
            else:
                # Fallback: just show the failing line
                lines.append(f"  {self._red('→')} {failing_code.strip()}")

            # Add pytest-style assertion output with E prefix
            if comparison and expected_value and actual_value:
                lines.append("")  # Blank line
                # Show "E  AssertionError: assert actual == expected"
                assertion_with_values = self._substitute_values_in_assertion(
                    failing_code.strip(), comparison, actual_value, expected_value
                )
                if assertion_with_values:
                    lines.append(self._red(f"E    AssertionError: {assertion_with_values}"))
                else:
                    lines.append(self._red("E    AssertionError"))

                # Show "E  Expected: X"
                lines.append(self._red(f"E    Expected: {expected_value}"))
                # Show "E  Received: X"
                lines.append(self._red(f"E    Received: {actual_value}"))

        return "\n".join(lines)

    def _get_source_context(
        self, file_path: Optional[str], line_number: Optional[int], num_lines: int = 3
    ) -> Optional[List[Tuple[int, str]]]:
        """
        Read the source file and get context lines around the failing line.

        Returns a list of (line_number, code) tuples.
        """
        if not file_path or not line_number:
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                all_lines = f.readlines()

            # Get lines before the failing line (num_lines before)
            start_idx = max(0, line_number - num_lines - 1)
            end_idx = line_number  # Inclusive of the failing line

            context = []
            for i in range(start_idx, end_idx):
                if i < len(all_lines):
                    # Line numbers are 1-indexed
                    context.append((i + 1, all_lines[i].rstrip()))

            return context if context else None
        except (IOError, OSError):
            # File not readable
            return None

    def _substitute_values_in_assertion(
        self, assertion: str, comparison: Comparison, actual_value: str, expected_value: str
    ) -> Optional[str]:
        """
        Substitute actual runtime values into the assertion statement.

        For example: "assert result == expected" becomes "assert 'foo' == 'bar'"
        """
        if not comparison:
            return None

        left_var = comparison["left"]
        operator = comparison["operator"]
        right_var = comparison["right"]

        # Replace variable names with actual values
        # For == comparisons, left is actual, right is expected
        substituted = assertion.replace(f"assert {left_var}", f"assert {actual_value}", 1)
        substituted = substituted.replace(
            f"{operator} {right_var}", f"{operator} {expected_value}", 1
        )

        # Clean up if it has a trailing message
        if "," in substituted:
            substituted = substituted.split(",")[0].strip()

        return substituted

    def _format_comparison(self, comparison: Comparison) -> str:
        """Format a comparison (expected vs actual) in a readable way."""
        lines = []
        lines.append(
            f"\n{self._dim('Expression:')} {comparison['left']} {comparison['operator']} {comparison['right']}"
        )

        # Try to make it clear what was compared
        op_explanations = {
            "==": "should equal",
            "!=": "should not equal",
            ">": "should be greater than",
            "<": "should be less than",
            ">=": "should be greater than or equal to",
            "<=": "should be less than or equal to",
            "in": "should be in",
            "not in": "should not be in",
            "is": "should be",
            "is not": "should not be",
        }

        explanation = op_explanations.get(comparison["operator"], comparison["operator"])
        lines.append(
            f"  {self._cyan(comparison['left'])} {self._dim(explanation)} {self._cyan(comparison['right'])}"
        )

        return "\n".join(lines)

    def _format_expected_received(self, expected: Optional[str], actual: Optional[str]) -> str:
        """Format expected vs received values (vitest-style)."""
        lines = []

        if expected is not None or actual is not None:
            lines.append("")  # Empty line for spacing

            if expected is not None:
                lines.append(f"{self._green('Expected:')} {self._format_value(expected)}")

            if actual is not None:
                lines.append(f"{self._red('Received:')} {self._format_value(actual)}")

        return "\n".join(lines)

    def _format_value(self, value: str) -> str:
        """Format a value for display, handling strings vs other types."""
        # If it looks like a string literal (quoted), keep the quotes
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            return value

        # Otherwise, try to make it readable
        # If it's a simple value, just return it
        return value

    def _format_stack_trace(self, stack_frames: List[StackFrame]) -> str:
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
        lines = message.strip().split("\n")
        formatted = []

        for line in lines:
            if line.strip().startswith('File "'):
                formatted.append(self._dim(line))
            elif ":" in line and not line.startswith(" ") and line[0].isupper():
                # Likely the exception line
                parts = line.split(":", 1)
                formatted.append(f"{self._red(parts[0])}: {parts[1] if len(parts) > 1 else ''}")
            elif line.strip().startswith("^"):
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
