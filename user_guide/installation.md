# Installation

Rustest supports Python **3.12 through 3.14**.

::: {.callout-warning title="The current release is a pre-release, and you have to ask for it by name"}
`1.0.0rc1` is a release candidate. pip and uv skip pre-releases unless told otherwise,
so a plain `pip install rustest` installs the previous stable version instead of this
one. Every command below names the version for that reason.
:::

## Using pip

```bash
pip install "rustest==1.0.0rc1"
```

Or let pip consider pre-releases generally:

```bash
pip install --pre rustest
```

## Using uv

If you use [uv](https://github.com/astral-sh/uv), add rustest to your project:

```bash
uv add "rustest==1.0.0rc1"
```

An explicit pre-release version needs no extra flag. To let uv choose one on its own:

```bash
uv pip install --prerelease allow rustest
```

## Verifying installation

`--version` prints the installed version and runs nothing:

```bash
rustest --version
```

Or run it as a Python module:

```bash
python -m rustest --version
```

## For development

If you want to contribute to rustest or modify it for your needs, see the [Development Guide](development.md) for setup instructions.

## Next steps

Now that you have rustest installed, head over to the [Quick Start](quickstart.md) guide to write your first tests.
