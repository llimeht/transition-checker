#!/usr/bin/python3

from __future__ import annotations

import sys
from pathlib import Path

try:
    from transitionchecker.cli.add_offerings_cli import main as _main
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
    from transitionchecker.cli.add_offerings_cli import main as _main


def main(argv: list[str] | None = None) -> int:
    return _main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
