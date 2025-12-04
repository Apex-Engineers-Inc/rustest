# Reporting

Test result objects returned by the `run()` function.

## RunReport

::: rustest.reporting.RunReport

### Attributes

#### total
**Type:** `int`

Total number of tests executed (passed + failed + skipped).

#### passed
**Type:** `int`

Number of tests that passed.

#### failed
**Type:** `int`

Number of tests that failed.

#### skipped
**Type:** `int`

Number of tests that were skipped.

#### duration
**Type:** `float`

Total execution time in seconds for all tests.

#### results
**Type:** `tuple[TestResult, ...]`

Tuple of individual test results. Each result is a [`TestResult`](#testresult) object.

### Methods

#### iter_status

**Signature:**
<!--rustest.mark.skip-->
```python
def iter_status(self, status: str) -> Iterable[TestResult]:
    ...
```

Yield results with the requested status.

**Parameters:**
- `status`: One of `"passed"`, `"failed"`, or `"skipped"`

**Returns:** Iterator of `TestResult` objects matching the status.

**Example:**

<!--rustest.mark.skip-->
```python
from rustest import run

report = run(paths=["tests"])

# Get all failed tests
for test in report.iter_status("failed"):
    print(f"{test.name}: {test.message}")

# Get all passed tests
for test in report.iter_status("passed"):
    print(f"{test.name} passed in {test.duration:.3f}s")
```

## TestResult

::: rustest.reporting.TestResult

### Attributes

#### name
**Type:** `str`

The test name (e.g., `"test_user_login"`, `"test_square[case_0]"`).

#### path
**Type:** `str`

The file path where the test is defined (e.g., `"tests/test_user.py"`).

#### status
**Type:** `str`

The test status. One of:
- `"passed"`: Test passed
- `"failed"`: Test failed
- `"skipped"`: Test was skipped

#### duration
**Type:** `float`

Execution time in seconds for this test.

#### message
**Type:** `str | None`

Error message if the test failed, `None` otherwise.

For failed tests, contains the exception message and traceback.

#### stdout
**Type:** `str | None`

Captured stdout output from the test (if `capture_output=True`), `None` otherwise.

#### stderr
**Type:** `str | None`

Captured stderr output from the test (if `capture_output=True`), `None` otherwise.

## Examples

### Basic Usage

<!--rustest.mark.skip-->
```python
from rustest import run

report = run(paths=["tests"])

# Summary statistics
print(f"Total: {report.total}")
print(f"Passed: {report.passed}")
print(f"Failed: {report.failed}")
print(f"Duration: {report.duration:.3f}s")
```

### Accessing Individual Results

<!--rustest.mark.skip-->
```python
from rustest import run

report = run(paths=["tests"])

for result in report.results:
    print(f"{result.name} ({result.status})")
    print(f"  Path: {result.path}")
    print(f"  Duration: {result.duration:.3f}s")

    if result.status == "failed":
        print(f"  Error: {result.message}")

    if result.stdout:
        print(f"  Output: {result.stdout}")
```

### Filtering Results

<!--rustest.mark.skip-->
```python
from rustest import run

report = run(paths=["tests"])

# Find failed tests
failed = [r for r in report.results if r.status == "failed"]
print(f"Failed tests: {len(failed)}")

# Find slow tests
slow = [r for r in report.results if r.duration > 1.0]
print(f"Slow tests: {len(slow)}")

# Find tests with output
with_output = [r for r in report.results if r.stdout or r.stderr]
print(f"Tests with output: {len(with_output)}")
```

### Using iter_status

<!--rustest.mark.skip-->
```python
from rustest import run

report = run(paths=["tests"])

# Failed tests only
print("Failed tests:")
for test in report.iter_status("failed"):
    print(f"  ❌ {test.name}")
    print(f"     {test.message}")

# Skipped tests only
print("\nSkipped tests:")
for test in report.iter_status("skipped"):
    print(f"  ⏭️  {test.name}")
```

### Creating Reports

<!--rustest.mark.skip-->
```python
from rustest import run
import json

report = run(paths=["tests"])

# Create JSON report
results_json = {
    "summary": {
        "total": report.total,
        "passed": report.passed,
        "failed": report.failed,
        "skipped": report.skipped,
        "duration": report.duration,
    },
    "tests": [
        {
            "name": r.name,
            "status": r.status,
            "duration": r.duration,
            "message": r.message,
        }
        for r in report.results
    ]
}

with open("test-results.json", "w") as f:
    json.dump(results_json, f, indent=2)
```

### Calculate Statistics

<!--rustest.mark.skip-->
```python
from rustest import run

report = run(paths=["tests"])

# Pass rate
pass_rate = (report.passed / report.total) * 100
print(f"Pass rate: {pass_rate:.1f}%")

# Average test duration
avg_duration = report.duration / report.total
print(f"Average test duration: {avg_duration:.3f}s")

# Slowest tests
slowest = sorted(report.results, key=lambda r: r.duration, reverse=True)[:5]
print("Slowest tests:")
for test in slowest:
    print(f"  {test.name}: {test.duration:.3f}s")
```

## Event Types (Advanced)

For building custom output renderers, rustest emits events during test collection and execution. These events are available from the `rustest.rust` module.

### Collection Events

Events emitted during the test discovery phase:

| Event | Description | Attributes |
|-------|-------------|------------|
| `CollectionStartedEvent` | Emitted when collection begins | `timestamp` |
| `CollectionProgressEvent` | Emitted as each file is collected | `file_path`, `tests_collected`, `files_collected`, `timestamp` |
| `CollectionCompletedEvent` | Emitted when collection finishes | `total_files`, `total_tests`, `duration`, `timestamp` |

### Execution Events

Events emitted during test execution:

| Event | Description | Attributes |
|-------|-------------|------------|
| `SuiteStartedEvent` | Test suite begins | `total_files`, `total_tests`, `timestamp` |
| `FileStartedEvent` | Test file begins | `file_path`, `total_tests`, `timestamp` |
| `TestCompletedEvent` | Individual test completes | `test_id`, `file_path`, `test_name`, `status`, `duration`, `message`, `timestamp` |
| `FileCompletedEvent` | Test file completes | `file_path`, `passed`, `failed`, `skipped`, `duration`, `timestamp` |
| `SuiteCompletedEvent` | Test suite completes | `passed`, `failed`, `skipped`, `errors`, `duration`, `timestamp` |
| `CollectionErrorEvent` | Collection error (e.g., syntax error) | `path`, `message`, `timestamp` |

### Example: Custom Event Consumer

<!--rustest.mark.skip-->
```python
from rustest import rust
from rustest.event_router import EventRouter

class MyConsumer:
    def handle(self, event):
        if isinstance(event, rust.CollectionStartedEvent):
            print("Starting collection...")
        elif isinstance(event, rust.CollectionProgressEvent):
            print(f"Found {event.tests_collected} tests in {event.file_path}")
        elif isinstance(event, rust.CollectionCompletedEvent):
            print(f"Collected {event.total_tests} tests in {event.duration:.2f}s")

# Use with event router
router = EventRouter()
router.subscribe(MyConsumer())

# Pass to rust.run() as event_callback=router.emit
```

## See Also

- [run()](core.md#run) - Function that returns these objects
- [Python API Guide](../guide/python-api.md) - Detailed usage examples
