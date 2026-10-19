# Installation

Rustest supports Python **3.10 through 3.14**.

## Using pip

The simplest way to install rustest is using pip:

```bash
pip install rustest
```

## Using uv

If you're using [uv](https://github.com/astral-sh/uv), you can add rustest to your project:

```bash
uv add rustest
```

## Platforms

Prebuilt wheels are published for Linux (x86_64 and aarch64, `manylinux` and
`musllinux`), macOS arm64 and Windows amd64. On any other platform, or for a Python
version without a wheel, pip falls back to building the source distribution, which needs a
C compiler and a Rust toolchain (for example `build-essential` and [rustup](https://rustup.rs)
in a Debian-based container).

## Verifying Installation

After installation, verify that rustest is working correctly:

```bash
rustest --version
```

Or run it as a Python module:

```bash
python -m rustest --version
```

## For Development

If you want to contribute to rustest or modify it for your needs, see the [Development Guide](../advanced/development.md) for setup instructions.

## Next Steps

Now that you have rustest installed, head over to the [Quick Start](quickstart.md) guide to write your first tests!
