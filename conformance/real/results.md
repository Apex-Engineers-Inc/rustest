| Suite | Mode | Verdict | Tests | pytest s | pytest -n 4 s | rustest s | x pytest | x xdist | engine only s |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| attrs | handed off: tests/conftest.py::pytest_configure is a pytest hook rustest does not run | MATCH | 1387 | 12.0 | 8.41 | 10.65 | 1.13x | 0.79x | 5.13 (MATCH) |
| cachetools | native | MATCH | 291 | 5.66 | 7.78 | 5.05 | 1.12x | 1.54x |  |
| click | native | MATCH | 1686 | 6.08 | 6.26 | 2.71 | 2.24x | 2.31x |  |
| dateutil | handed off: tests/conftest.py::pytest_collection_modifyitems is a pytest hook rustest does not run | MATCH | 2095 | 6.27 | 7.03 | 8.2 | 0.76x | 0.86x | 2.36 (MATCH) |
| humanize | native | MATCH | 784 | 2.85 | 4.16 | 1.73 | 1.65x | 2.40x |  |
| jsonschema | native | MATCH | 7815 | 18.22 | 8.84 | 7.74 | 2.35x | 1.14x |  |
| marshmallow | native | EXPLAINED | 1178 | 2.66 | 3.67 | 1.27 | 2.09x | 2.89x |  |
| more-itertools | native | MATCH | 722 | 9.29 | 8.26 | 5.68 | 1.64x | 1.45x |  |
| pillow | handed off: conformance/real/_work/pillow/Tests/conftest.py::pytest_configure is a pytest hook rustest does not run | MATCH | 4036 | 68.08 | 21.3 | 24.34 | 2.80x | 0.88x | 35.59 (MATCH) |
| rich | native | EXPLAINED | 1007 | 7.94 | 6.25 | 4.25 | 1.87x | 1.47x |  |
| sqlparse | native | MATCH | 482 | 2.99 | 4.72 | 1.43 | 2.09x | 3.30x |  |
