#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from ocmemog.doctor import main


if __name__ == "__main__":
    raise SystemExit(main())
