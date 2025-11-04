"""Module executed when running ``python -m rustest``."""

from __future__ import annotations

import sys

from ._cli import main

if __name__ == "__main__":
    sys.exit(main())
