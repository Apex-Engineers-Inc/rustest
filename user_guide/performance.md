# Performance

This page is the evidence behind rustest's speed claims. It is deliberately not a
marketing page: it reports what was measured, on what, under what conditions, and what
those numbers do and do not license you to expect.

**The short version.** On seventeen real open-source pytest suites, rustest ran between
**1.1x and 5.7x** faster than pytest. Aggregated over all seventeen the figure is
**1.23x**, because two of those suites are almost entirely their own code and a test
runner cannot make your code faster. Across the other fifteen it is **2.74x**.

There is no single "rustest is Nx faster" number, and this page will not give you one.
What determines your speedup is measurable in advance, and the next section is how to
measure it.

## What a faster runner can actually win

A test run is your code plus the framework around it. rustest can only make the framework
part faster. So the ceiling on any speedup is the fraction of the run that *is* framework
— and that fraction was measured per suite rather than assumed:

| Suite | Time spent in your test bodies | Framework share = the ceiling |
|---|---:|---:|
| werkzeug | 99% | 1% |
| member-designer (private, 6,132 tests) | 96% | 4% |
| more-itertools | 87% | 13% |
| jsonschema | 76% | 24% |
| attrs | 74% | 26% |
| rich | 74% | 26% |
| cachetools | 51% | 49% |
| dateutil | 30% | 70% |
| jinja2 | 28% | 72% |
| sqlparse | 26% | 74% |
| click | 22% | 78% |
| marshmallow | 10% | 90% |

Read that table before the one below. **A 1.2x on cachetools and a 6x on sqlparse are the
same result** — each is most of the framework share that suite had to give. If your suite
spends 95% of its wall clock inside your own functions (network calls, database
round-trips, numeric work), no runner can give you more than a few percent, and any tool
that promises otherwise is measuring something other than your suite.

Body share is measured at `-n 1`; a pooled run can exceed 100% because several workers'
bodies run inside one wall-clock second, which is why some targets are absent from the
list.

## The seventeen-suite sweep

Each target is a real, unmodified open-source pytest suite at a pinned revision, run
**sequentially** — one target at a time, each target's two runners one after the other and
never concurrently, because wall-clock is what this table reports and a suite competing for
cores reports a time that means nothing. Both columns are wall-clock measured around the
subprocess, so they are directly comparable to each other.

Verdicts are the harness's: **MATCH** = every node id and every outcome count identical to
pytest's; **EXPLAINED** = every remaining difference is covered by a ledger entry naming
its mechanism; **DIVERGE** = something is not.

| # | Suite | Verdict | pytest | rustest | Speedup | Framework share | Notes |
|---|---|---|---:|---:|---:|---:|---|
| 1 | attrs | MATCH | 18.37s | 6.27s | **2.93x** | 26% | 1,387 tests |
| 2 | cachetools | MATCH | 5.74s | 4.83s | **1.19x** | 49% | 291 tests; capped at 1.11x by file granularity |
| 3 | click | MATCH | 6.32s | 2.53s | **2.50x** | 78% | 1,686 ids, 31,000 deselected |
| 4 | dateutil | MATCH | 7.55s | 2.05s | **3.68x** | 70% | 2,095 tests |
| 5 | fastapi | EXPLAINED | 76.59s | 13.53s | **5.66x** | — | 3,289 ids; an anyio deselection asymmetry |
| 6 | humanize | MATCH | 2.88s | 1.48s | **1.95x** | — | 784 ids |
| 7 | jinja2 | MATCH | 4.59s | 1.93s | **2.38x** | 72% | 909 tests |
| 8 | jsonschema | MATCH | 28.37s | 9.76s | **2.91x** | 24% | 7,815 ids; pinned `-n 1` for correctness |
| 9 | marshmallow | EXPLAINED | 3.69s | 1.35s | **2.73x** | 90% | 4 ids: the target puts a wall-clock in a node id |
| 10 | member-designer | MATCH | 309.68s | 280.61s | **1.10x** | 4% | 6,132 ids, full parity; median of five runs — see below |
| 11 | more-itertools | MATCH | 11.36s | 6.25s | **1.82x** | 13% | 722 tests |
| 12 | pillow | MATCH | 89.64s | 41.26s | **2.17x** | — | 4,036 ids |
| 13 | psutil | EXPLAINED | 1948.05s | 1663.86s | **1.17x** | — | 713 ids, 712 statuses identical |
| 14 | pynite | MATCH | 94.12s | 27.08s | **3.48x** | — | 181 tests, numeric/FEA |
| 15 | rich | EXPLAINED | 9.44s | 4.79s | **1.97x** | 26% | 1 id: `test_suppress`, a known limitation |
| 16 | sqlparse | MATCH | 3.61s | 1.62s | **2.23x** | 74% | 482 ids incl. 2 xfailed + 1 xpassed |
| 17 | werkzeug | MATCH | 29.45s | 18.17s | **1.62x** | 1% | 969 ids; rustest leaks 0 processes, pytest leaks 6 |

**Scoreboard: 13 MATCH, 4 EXPLAINED, 0 DIVERGE.** Seventeen of seventeen graded.

The four EXPLAINED rows are worth reading, because none of them is rustest getting a test
result wrong:

- **fastapi** — the partial run deselects a different count under the `anyio` plugin
  (18 vs 9), by design of how the target is invoked.
- **marshmallow** and **psutil** — the *target's* own nondeterminism. marshmallow embeds a
  wall-clock reading in a node id; psutil's single differing test is a live CPU-frequency
  reading, and it **failed on pytest's side and passed on rustest's**.
- **rich** — `test_suppress`, the one class of test rustest structurally cannot pass: it
  introspects the *identity* of the `pytest` module object rather than using its API. See
  [pytest compatibility](pytest-compat.md).

### The aggregate, and why it is not the headline

**Totals across all seventeen: pytest 2,683.6s, rustest 2,184.5s — 1.23x.**

That figure is dominated by the two suites that are almost entirely body time: psutil alone
is 1,948s of pytest's total, and member-designer another 310s. Across the other fifteen,
pytest spends **391.7s** and rustest **142.9s** — **2.74x**.

Both numbers are real, and neither answers "how much faster will *my* suite be". That
answer is in the framework-share table at the top.

**Do not average the per-suite ratios.** A mean over seventeen suites is a statement about
which projects happened to be picked, not about the runner.

### member-designer: the row that went the wrong way, then didn't

This target — a private 6,132-test suite pinned to `-n 1` because it shares a MongoDB — was
the sweep's one honest loss. rustest ran it at **0.72x**: genuinely *slower* than pytest.
The cause was real and fixable: session- and package-scoped fixtures were being rebuilt
once per *file* rather than once per run, and this suite's root `conftest.py` holds five
expensive ones (a service manager, a database drop-and-reinitialise, a
`ProcessPoolExecutor`, an ASGI transport). At `-n 1` across ~285 files, that cost was paid
285 times.

After the fix, five runs give medians of **486.0s → 280.6s, i.e. 0.72x → 1.10x** —
rustest crossing pytest on this target for the first time. Per-file fixture-registry
construction went from 71.9µs to 1.2µs.

Two honesty notes, because this is the one figure on the page produced by an A/B rather
than a single measurement window. The pytest control (unchanged code) spans 290.33–395.65s
across the five runs, so this target carries roughly ±15% run-to-run and **no single pair
of runs is worth quoting** — the medians are. And the arms are confounded with run order
(the pre-fix arm occupies the even slots, n=2 against n=3). The effect is far larger than
that bias could plausibly account for and the mechanism explains it independently, but a
replication should randomise the interleaving.

## Component numbers

The two things rustest actually optimises, measured on a generated 500-file / 5,000-test
suite:

| Component | pytest | rustest | Ratio |
|---|---:|---:|---:|
| Warm collection | 8.39s | 227.6ms | **~37x** |
| Marginal per-test framework overhead | 933.6µs | 117.9µs | **~8x** |

Collection is where the Rust core earns its keep: an AST-based collection tier that never
imports your modules, behind a content-addressed manifest cache. Marginal overhead is what
is left per test once the fixed per-run costs (spawning the worker pool, booting CPython in
each worker) are subtracted out.

!!! warning "The overhead metric is noisy on ordinary hardware"
    The marginal-overhead figure above is the tracked gate measurement. When the benchmark
    baselines were later regenerated on a machine carrying ordinary background load, the
    same metric read 178µs/test — and **that move must not be read as a regression**. In
    the investigation that followed, a byte-identical control build reported 93.6, 61.9,
    37.1, 94.1, 16.0 and 37.0µs/test across six consecutive runs. A metric whose unchanged
    control swings 6x cannot resolve a 40µs effect. Quote this number with that caveat, or
    not at all.

### Synthetic baselines

Generated all-passing suites, regenerated on a quiet machine, tracked in
[`conformance/baselines.json`](https://github.com/Apex-Engineers-Inc/rustest/blob/main/conformance/baselines.json):

| Files | Tests | pytest run | rustest run | Speedup |
|---:|---:|---:|---:|---:|
| 10 | 100 | 1.07s | 0.71s | 1.5x |
| 100 | 1,000 | 2.10s | 1.10s | 1.9x |
| 500 | 5,000 | 8.30s | 2.07s | **4.0x** |

The shape of that column is the point. rustest pays a fixed cost per run — spawning a
worker pool, booting an interpreter in each. On a hundred tests that fixed cost is most of
the run. On five thousand it disappears, and what is left is the per-test overhead ratio.

Full methodology — suite generation, command order, the ordering-bias caveat — and the raw
data are in
[`conformance/README.md`](https://github.com/Apex-Engineers-Inc/rustest/blob/main/conformance/README.md).

## Measuring conditions

Stated rather than implied, because they bound what the absolute seconds mean:

- **The machine is not idle and cannot be made idle.** A container backend holds roughly
  1.5 cores throughout and a browser about 0.5, measured immediately before the sweep.
  That is a tax on **both** columns, so the ratios are sound while the absolute seconds are
  an upper bound on what the hardware can do.
- Every target's virtualenv was reinstalled from wheels built from the measured tree, and
  each was verified by the target's *own* interpreter to be importing the compiled
  extension under test — so no row is measuring a stale build.
- Two targets are pinned to `-n 1` for correctness, not for speed: jsonschema has a proven
  import-order dependency, and member-designer shares a MongoDB. jsonschema's pin costs it
  roughly a third of its wall clock, and that cost is visible in its row.

## Getting the most out of it

- **Use the worker pool.** `-n` defaults to 4, capped by CPU count. Work is distributed at
  file granularity, so a suite whose tests are concentrated in one enormous file cannot be
  parallelised past that file — splitting it is a real speedup. (This is what caps
  cachetools at 1.11x however many workers exist.)
- **Use `--lf` while iterating.** Warm collection is ~230ms, so rerunning just the failed
  tests is close to instant. Paired with [`--llm`](llm-output.md) this is the tight loop
  that agent tooling wants.
- **`--cov` is opt-in and costs nothing when off.** Coverage runs through `sys.monitoring`
  rather than a plugin; a run that does not ask for it registers no monitoring tool at all.
- **Reduce your own body time first.** If the framework-share table says your suite is 95%
  body, profile the bodies. That is not a rustest limitation, it is arithmetic.

## Reproducing this

Everything above is regenerated by the repository's own conformance harness:

```bash
# The synthetic baselines table
uv run python -m conformance --bench

# One real-world target, or all seventeen
uv run python -m conformance --real sqlparse
uv run python -m conformance --real all
```

The `--real` sweep clones each target at its pinned revision, builds a wheel for that
target's interpreter, runs both runners sequentially, and grades node ids and outcome
counts against pytest's *before* reporting a time — so a fast run that got a different
answer is reported as a divergence rather than as a win.
