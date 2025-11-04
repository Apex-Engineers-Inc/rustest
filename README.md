# rustest

Rustest is a Rust-powered test runner that aims to provide the most common
pytest ergonomics with a focus on raw performance. The project ships with a
clean Python API, a friendly CLI, and a simple build pipeline powered by
[`uv`](https://github.com/astral-sh/uv), [`maturin`](https://github.com/PyO3/maturin),
and [`poethepoet`](https://github.com/nat-n/poethepoet).

## Features

- ✅ Familiar `@fixture`, `@parametrize`, and `@skip` helpers.
- ✅ Test discovery for files named `test_*.py` or `*_test.py`.
- ✅ Dependency-injected fixtures resolved by Rust for minimal overhead.
- ✅ Optional stdout/stderr capture and pretty CLI output.
- ✅ Fully typed Python API with `mypy` configuration ready to go.

## Installation

Rustest targets Python **3.10+**. The recommended workflow uses `uv` which acts
as both the dependency resolver and the virtual environment manager.

```bash
uv sync --all-extras
uv run maturin develop
```

The first command installs the Python dependencies declared in
`pyproject.toml`; the second compiles and installs the Rust extension in the
current environment.

## Usage

Running the CLI mirrors pytest's ergonomics:

```bash
uv run rustest tests/
```

or directly from Python:

```python
from rustest import run

report = run(paths=["tests"], pattern=None, workers=None, capture_output=True)
print(report)
```

## Development workflow

Common commands are exposed via `poe` tasks:

| Task      | Description                            |
| --------- | -------------------------------------- |
| `poe dev` | Build the Rust extension in develop mode. |
| `poe lint` | Run Ruff on the Python sources. |
| `poe typecheck` | Run mypy with the bundled strict settings. |
| `poe pytests` | Run the Python unit tests covering the high-level API. |
| `poe fmt` | Format the Rust code using `cargo fmt`. |
| `poe unit` | Execute the example test suite. |

All tasks internally invoke `uv` to guarantee reproducible environments.

The Python tests are executed as a package (via `python -m unittest discover -s python/tests -t python`).
During startup the shared helpers invoke `maturin develop` (through `uv run` when
available) to install the project into the active environment so the suite
exercises the same layout that end users rely on. If building the extension is
not possible—such as in sandboxed environments—the helpers gracefully fall back
to the checked-in sources while still stubbing the compiled module for import
purposes.

## Example

A minimal test suite can look like this:

```python
from rustest import fixture, parametrize

@fixture
def numbers() -> list[int]:
    return [1, 2, 3]

@parametrize("value", [(1,), (2,), (3,)])
def test_numbers(numbers: list[int], value: int) -> None:
    assert value in numbers
```

Running `uv run rustest` executes the test with Rust-speed discovery and a clean
summary.

## License

rustest is distributed under the terms of the MIT license. See [LICENSE](LICENSE).
