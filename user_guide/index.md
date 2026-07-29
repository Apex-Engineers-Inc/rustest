---
title: User Guide
---

# rustest

A Rust-powered test runner for Python that runs your existing pytest suite unchanged.

```bash
pip install rustest
rustest tests/
```

There is no flag to enable pytest compatibility — it is the only behaviour. `import pytest`
resolves to rustest's own implementation on every run, so an existing suite just runs.

## Which describes you?

### New to testing

Start at [Why automated testing?](intro-why-test.md). The beginner track assumes nothing:
it walks from "why bother" through your first test, fixtures, parametrization and how to
organise a growing suite.

1. [Why automated testing?](intro-why-test.md)
2. [Testing basics](intro-testing-basics.md)
3. [Your first test](intro-first-test.md)
4. [Making tests reusable](intro-fixtures.md)
5. [Organizing your tests](intro-organizing.md)
6. [Testing multiple cases](intro-parametrization.md)

### Coming from pytest

Run `rustest tests/` and see what happens — for most suites, that is the whole migration.
Then read:

- [pytest compatibility](pytest-compat.md) — the honest gap list, including the things
  rustest does not do and why
- [Comparison with pytest](comparison.md) — feature by feature
- [Plugin replacements](pytest-plugins.md) — rustest supports no plugins by design; this is
  what replaces the ten most popular ones
- [Upgrade guide](migration-guide.md) — if you are coming from an older rustest, this is
  the page with the breaking changes

### Already running rustest

- [CLI usage](cli.md) — every flag
- [LLM output](llm-output.md) — JSONL for agents and tooling, and the `--llm --lf` loop
- [Performance](performance.md) — the seventeen-suite measurement, and how to work out what
  *your* suite will get
- [API Reference](../reference/index.html) — generated from the source

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

## What you get

| | |
|---|---|
| **Drop-in** | `@fixture`, `@parametrize`, `@mark`, `raises`, `approx` — and `import pytest` works |
| **Async built in** | `@mark.asyncio` with pytest-asyncio's loop-scope model. No plugin |
| **Mocking built in** | The `mocker` fixture. No pytest-mock |
| **Coverage built in** | `--cov` through `sys.monitoring`. No pytest-cov |
| **Markdown testing** | Python fences in `.md` files run as tests — this site's examples are tested that way |
| **Machine-readable** | `--llm` JSONL, `--report-json`, and pytest's exit codes |
| **Honest failures** | pytest-shaped `FAILURES` / `short test summary info` sections, with assertion rewriting |

## Where it is fast, and where it is not

rustest can only speed up the *framework* part of a run, never your own test bodies. On
seventeen real open-source suites it ranged from **1.1x to 5.7x**, and the number a given
suite gets is predictable from how much of its time is framework rather than body.
[Performance](performance.md) has the full table, the method, and the caveats — including
the one suite where rustest was slower until a fix landed.

## Production notes

- MIT licensed
- Python 3.12 – 3.14
- No plugin system, by design — [the reasoning](pytest-plugins.md)
- [Known gaps](pytest-compat.md), kept current and cross-checked against a conformance
  corpus that diffs rustest against real pytest

## Community

- [GitHub repository](https://github.com/Apex-Engineers-Inc/rustest)
- [Issue tracker](https://github.com/Apex-Engineers-Inc/rustest/issues)
- [Development guide](development.md) — how to build and contribute
- [License](https://github.com/Apex-Engineers-Inc/rustest/blob/main/LICENSE)
