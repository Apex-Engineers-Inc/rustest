| Suite | Mode | Verdict | Tests | pytest s | pytest -n 4 s | rustest s | record | x pytest | x xdist | engine only s |
|---|---|---|---:|---:|---:|---:|---|---:|---:|---:|
| attrs | native | MATCH | 1387 | 16.31 | 8.07 | 5.82 |  | 2.80x | 1.39x |  |
| cachetools | native | MATCH | 291 | 5.8 | 9.28 | 5.3 |  | 1.09x | 1.75x |  |
| click | native | MATCH | 1686 | 6.72 | 5.41 | 3.29 |  | 2.04x | 1.64x |  |
| dateutil | native | MATCH | 2095 | 7.39 | 5.24 | 3.51 |  | 2.11x | 1.49x |  |
| fastapi | handed off: the pytest plugin inline_snapshot (inline_snapshot-0.34.2) is installed and rustest does not run it (`-p no:inline_snapshot` disables it) | MATCH | 3289 | 86.09 | 20.94 | 21.36 |  | 4.03x | 0.98x | 14.2 (EXPLAINED) |
| humanize | native | MATCH | 784 | 4.45 | 3.67 | 1.84 |  | 2.42x | 1.99x |  |
| jinja2 | native | MATCH | 909 | 6.2 | 4.43 | 2.12 |  | 2.92x | 2.09x |  |
| jsonschema | native | MATCH | 7815 | 15.66 | 8.2 | 8.3 |  | 1.89x | 0.99x |  |
| marshmallow | native | EXPLAINED | 1178 | 3.62 | 4.37 | 1.76 |  | 2.06x | 2.48x |  |
| member-designer | handed off: rustest's hook runtime: tests.slow_guard::pytest_configure uses config.pluginmanager.register, which rustest's hook runtime does not support | MATCH | 11591 | 967.61 | 230.4 | 293.42 |  | 3.30x | 0.79x | 26.86 (DIVERGE) |
| more-itertools | native | MATCH | 722 | 9.71 | 8.07 | 6.21 |  | 1.56x | 1.30x |  |
| pillow | native | MATCH | 4036 | 59.26 | 20.15 | 12.76 | warm | 4.64x | 1.58x |  |
| psutil | native | DIVERGE | 713 | 576.84 | 417.86 | 420.83 |  | 1.37x | 0.99x |  |
| pynite | native | MATCH | 182 | 66.87 | 23.89 | 20.85 |  | 3.21x | 1.15x |  |
| rich | native | EXPLAINED | 1007 | 6.96 | 6.32 | 4.19 |  | 1.66x | 1.51x |  |
| sqlparse | native | MATCH | 482 | 3.97 | 4.34 | 1.74 |  | 2.28x | 2.49x |  |
| werkzeug | handed off: the pytest plugin timeout is used (mark 'timeout'), and rustest does not run it (tests/test_debug.py::test_chained_exception_cycle) | MATCH | 964 | 27.19 | 16.52 | 19.13 |  | 1.42x | 0.86x | 18.07 (MATCH) |
