## 1. Agreement (rustest -n 4 against serial pytest, no ledger)

| suite | release | tests | same | differ | flaky under pytest | ran as |
|---|---|---|---|---|---|---|
| attrs | 26.1.0 | 1387 | 1387 | 0 | 0 | native |
| black | 26.10.1 | 654 | 654 | 0 | 0 | native |
| cachetools | v7.1.6 | 291 | 291 | 0 | 0 | native |
| click | 8.4.2 | 1686 | 1686 | 0 | 0 | native |
| dateutil | 2.9.0.post0 | 2095 | 2095 | 0 | 0 | native |
| fastapi | 0.140.7 | 3289 | 3288 | 1 | 3 | handed to pytest |
| flask | 3.1.3 | 490 | 490 | 0 | 0 | native |
| httpx | 0.28.1 | 1417 | 1417 | 0 | 0 | native |
| humanize | 4.16.0 | 784 | 784 | 0 | 0 | native |
| jinja2 | 3.1.6 | 909 | 909 | 0 | 0 | native |
| jsonschema | v4.26.0 | 7815 | 7815 | 0 | 0 | native |
| marshmallow | 4.3.0 | 1178 | 1166 | 6 | 1182 | native |
| more-itertools | v11.1.0 | 722 | 722 | 0 | 0 | native |
| networkx | networkx-3.7 | 10152 | 10152 | 0 | 0 | handed to pytest |
| packaging | 26.3 | 62423 | 62423 | 0 | 0 | native |
| pillow | 12.3.0 | 4036 | 4036 | 0 | 0 | native |
| pydantic | v2.14.4 | 11259 | 11259 | 0 | 0 | handed to pytest |
| pynite |  | 182 | 182 | 0 | 0 | native |
| requests | v2.34.2 | 633 | 633 | 0 | 0 | handed to pytest |
| rich | v15.0.0 | 1007 | 1006 | 1 | 0 | native |
| sqlparse | 0.5.5 | 482 | 482 | 0 | 0 | native |
| starlette | 1.10.0 | 1416 | 1416 | 0 | 0 | native |
| typer | 0.29.2 | 1665 | 1659 | 0 | 6 | native |
| urllib3 | 2.8.0 | 2673 | 2673 | 0 | 0 | handed to pytest |
| werkzeug | 3.1.8 | 964 | 964 | 0 | 0 | handed to pytest |

## 2. Speed, like for like (median seconds)

### One worker

| suite | pytest | rustest -n 1 | speedup |
|---|---|---|---|
| attrs | 11.54 | 11.71 | 0.99x |
| black | 94.68 | 99.41 | 0.95x |
| cachetools | 5.8 | 5.44 | 1.07x |
| click | 6.72 | 5.08 | 1.32x |
| dateutil | 7.03 | 4.17 | 1.69x |
| fastapi | 70.76 | 54.58 | 1.30x |
| flask | 8.09 | 6.32 | 1.28x |
| httpx | 25.43 | 21.39 | 1.19x |
| humanize | 3.13 | 2.73 | 1.15x |
| jinja2 | 4.03 | 3.84 | 1.05x |
| jsonschema | 21.75 | 10.67 | 2.04x |
| marshmallow | 3.25 | 2.5 | 1.30x |
| more-itertools | 10.03 | 9.03 | 1.11x |
| networkx | 142.57 | 152.37 | 0.94x |
| packaging | 55.72 | 16.47 | 3.38x |
| pillow | 61.43 | 57.92 | 1.06x |
| pydantic | 35.76 | 44.88 | 0.80x |
| pynite | 45.49 | 46.07 | 0.99x |
| requests | 173.15 | 177.95 | 0.97x |
| rich | 10.36 | 8.22 | 1.26x |
| sqlparse | 3.49 | 3.05 | 1.14x |
| starlette | 38.57 | 38.98 | 0.99x |
| typer | 915.92 | 928.81 | 0.99x |
| urllib3 | 156.33 | 164.5 | 0.95x |
| werkzeug | 33.77 | 37.57 | 0.90x |

### 4 workers

| suite | pytest -n 4 | rustest -n 4 | speedup |
|---|---|---|---|
| attrs | 10.12 | 5.01 | 2.02x |
| black | 43.63 | 41.63 | 1.05x |
| cachetools | 8.02 | 2.73 | 2.94x |
| click | 8.03 | 3.14 | 2.56x |
| dateutil | 8.15 | 3.68 | 2.21x |
| fastapi | 24.85 | 24.73 | 1.00x |
| flask | 18.57 | 4.08 | 4.55x |
| httpx | None | None |  |
| humanize | 4.8 | 2.05 | 2.34x |
| jinja2 | 5.04 | 2.18 | 2.31x |
| jsonschema | 10.35 | 9.96 | 1.04x |
| marshmallow | 6.72 | 1.82 | 3.69x |
| more-itertools | 9.17 | 5.42 | 1.69x |
| networkx | 45.46 | 45.76 | 0.99x |
| packaging | 32.34 | 11.37 | 2.84x |
| pillow | 23.0 | 18.45 | 1.25x |
| pydantic | 20.39 | 22.33 | 0.91x |
| pynite | 27.66 | 23.35 | 1.18x |
| requests | 71.87 | 72.43 | 0.99x |
| rich | 7.85 | 4.74 | 1.66x |
| sqlparse | 7.14 | 2.58 | 2.77x |
| starlette | 17.3 | 10.48 | 1.65x |
| typer | 249.99 | 223.63 | 1.12x |
| urllib3 | 58.37 | 56.42 | 1.03x |
| werkzeug | 24.59 | 25.15 | 0.98x |

## 3. Everything on (16 workers, warm cache)

| suite | pytest | pytest -n 16 | rustest -n 16 | vs serial | vs xdist |
|---|---|---|---|---|---|
| attrs | 11.54 | 17.19 | 9.26 | 1.25x | 1.86x |
| black | 94.68 | 45.1 | 38.59 | 2.45x | 1.17x |
| cachetools | 5.8 | 11.14 | 3.29 | 1.76x | 3.39x |
| click | 6.72 | 13.6 | 4.31 | 1.56x | 3.16x |
| dateutil | 7.03 | 13.65 | 7.44 | 0.94x | 1.83x |
| fastapi | 70.76 | 45.38 | 44.44 | 1.59x | 1.02x |
| flask | 8.09 | 24.87 | 9.12 | 0.89x | 2.73x |
| httpx | 25.43 | None | None |  |  |
| humanize | 3.13 | 8.56 | 1.86 | 1.68x | 4.60x |
| jinja2 | 4.03 | 9.02 | 2.97 | 1.36x | 3.04x |
| jsonschema | 21.75 | 13.45 | 10.52 | 2.07x | 1.28x |
| marshmallow | 3.25 | 8.65 | 6.84 | 0.48x | 1.26x |
| more-itertools | 10.03 | 12.34 | 6.3 | 1.59x | 1.96x |
| networkx | 142.57 | 59.01 | 56.64 | 2.52x | 1.04x |
| packaging | 55.72 | 44.07 | 13.2 | 4.22x | 3.34x |
| pillow | 61.43 | 34.95 | 20.45 | 3.00x | 1.71x |
| pydantic | 35.76 | 31.91 | 32.59 | 1.10x | 0.98x |
| pynite | 45.49 | 40.81 | 33.48 | 1.36x | 1.22x |
| requests | 173.15 | 39.06 | 39.55 | 4.38x | 0.99x |
| rich | 10.36 | 12.24 | 8.82 | 1.17x | 1.39x |
| sqlparse | 3.49 | 9.37 | 2.49 | 1.40x | 3.76x |
| starlette | 38.57 | 24.36 | 15.24 | 2.53x | 1.60x |
| typer | 915.92 | 200.65 | 237.3 | 3.86x | 0.85x |
| urllib3 | 156.33 | 48.58 | 49.69 | 3.15x | 0.98x |
| werkzeug | 33.77 | 14.48 | 20.22 | 1.67x | 0.72x |
