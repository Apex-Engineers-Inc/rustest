---
title: "Home"
body-classes: "gd-homepage"
---

<div class="rt-hero">
  <img src="assets/logo.svg" alt="rustest" class="rt-hero-logo" />
  <p class="rt-hero-tagline">A Rust-powered test runner that runs your existing
  pytest suite <em>unchanged</em>.</p>
  <div class="rt-hero-install"><code>pip install &quot;rustest==1.0.0rc1&quot;</code></div>
</div>

::: {.rt-cta}
[Quick start](user-guide/quickstart.md) [Coming from pytest](user-guide/pytest-compat.md) [GitHub](https://github.com/Apex-Engineers-Inc/rustest)
:::

There is no flag to enable pytest compatibility; it is the only behaviour. `import pytest`
resolves to rustest's own implementation on every run, so an existing suite just runs.

{{< termshow file="quickstart" autoplay="false" loop="false" >}}

That is a real run of `rustest examples/tests/ -v` in this repository, recorded rather than
transcribed. It plays back as SVG frames, so it stays sharp at any zoom and costs a fraction
of what the same clip would cost as a GIF.

::: {.callout-note title="A release candidate, and what it is for"}
The version is named explicitly above because pip and uv skip pre-releases by default, so a
plain `pip install rustest` still gives the previous stable release.

This release is the result of a ground-up rewrite of the engine, and the rewrite was
spent on **compatibility, stability and reliability**: being a runner that genuinely
agrees with pytest, measured against a conformance corpus and seventeen real open-source
suites. It is also faster than pytest, but that is not what this release is about, and
[Performance](user-guide/performance.md) reports the numbers rather than advertising them. Speed is
the next body of work.
:::

## Which describes you?

::: {.rt-cards}

::: {.rt-card}
::: {.rt-eyebrow}
New to testing
:::

**Start at [Why automated testing?](user-guide/intro-why-test.md)** The beginner track assumes nothing.
It walks from "why bother" through your first test, fixtures, parametrization and how to
organise a suite as it grows.

1. [Why automated testing?](user-guide/intro-why-test.md)
2. [Testing basics](user-guide/intro-testing-basics.md)
3. [Your first test](user-guide/intro-first-test.md)
4. [Making tests reusable](user-guide/intro-fixtures.md)
5. [Organizing your tests](user-guide/intro-organizing.md)
6. [Testing multiple cases](user-guide/intro-parametrization.md)
:::

::: {.rt-card}
::: {.rt-eyebrow}
Coming from pytest
:::

**Run `rustest tests/` and see what happens.** For most suites that is the whole migration.
Then read:

- [pytest compatibility](user-guide/pytest-compat.md), the honest gap list, including the things
  rustest does not do and why
- [Comparison with pytest](user-guide/comparison.md), feature by feature
- [Plugin replacements](user-guide/pytest-plugins.md). rustest supports no plugins by design; this is
  what replaces the ten most popular ones
- [Upgrade guide](user-guide/migration-guide.md), the breaking changes if you are coming from an older
  rustest
:::

::: {.rt-card}
::: {.rt-eyebrow}
Already running rustest
:::

**Go straight to the reference.**

- [CLI usage](user-guide/cli.md), every flag
- [LLM output](user-guide/llm-output.md), JSONL for agents and tooling, and the `--llm --lf` loop
- [Performance](user-guide/performance.md), the seventeen-suite measurement and how to work out what
  *your* suite will get
- [API Reference](reference/index.html), generated from the source
:::

:::

## What it looks like

```python
from rustest import fixture, parametrize, mark, raises


@fixture
def account():
    return {"balance": 100}


@parametrize("amount,expected", [(10, 110), (25, 125)])
def test_deposit(account, amount, expected):
    account["balance"] += amount
    assert account["balance"] == expected


@mark.asyncio
async def test_async_settlement():
    assert 42 == 42


def test_withdrawal_over_balance(account):
    with raises(KeyError):
        account["overdraft"]
```

Same decorators as pytest, and pytest's spellings work too. No plugin needed for `asyncio`,
mocking, or coverage.

## When something fails

A failing run is the output that matters, so it is worth seeing before you commit to a
runner. This one is recorded from a real run too: a wrong subtotal in a two-test file, then
`--lf` to re-run only what failed.

{{< termshow file="failure" autoplay="false" loop="false" >}}

The assertion is rewritten to show the values that produced it, the `FAILURES` block and
`short test summary info` are pytest's, and so is the exit code.

## What you get

| | |
|---|---|
| **Drop-in** | `@fixture`, `@parametrize`, `@mark`, `raises`, `approx`, and `import pytest` works |
| **Async built in** | `@mark.asyncio` with pytest-asyncio's loop-scope model. No plugin |
| **Mocking built in** | The `mocker` fixture. No pytest-mock |
| **Coverage built in** | `--cov` through `sys.monitoring`. No pytest-cov |
| **Markdown testing** | Python fences in `.md` files run as tests (off by default; `--codeblocks`). This site's examples are tested that way |
| **Machine-readable** | `--llm` JSONL, `--report-json`, and pytest's exit codes |
| **Honest failures** | pytest-shaped `FAILURES` and `short test summary info` sections, with assertion rewriting |

## Where it is fast, and where it is not

rustest can only speed up the *framework* part of a run, never your own test bodies. On
seventeen real open-source suites it ranged from **1.1x to 5.7x**, and the number a given
suite gets is predictable from how much of its time is framework rather than body.
[Performance](user-guide/performance.md) has the full table, the method, and the caveats, including the
one suite where rustest was slower until a fix landed.

## Production notes

- MIT licensed
- Python 3.12 to 3.14
- No plugin system, by design. [The reasoning](user-guide/pytest-plugins.md)
- [Known gaps](user-guide/pytest-compat.md), kept current and cross-checked against a conformance
  corpus that diffs rustest against real pytest

## Community

- [GitHub repository](https://github.com/Apex-Engineers-Inc/rustest)
- [Issue tracker](https://github.com/Apex-Engineers-Inc/rustest/issues)
- [Development guide](user-guide/development.md), how to build and contribute
- [License](https://github.com/Apex-Engineers-Inc/rustest/blob/main/LICENSE)
