#!/usr/bin/env python3

from __future__ import annotations

import sys

from ddi_data_model.sidecars.validate import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
