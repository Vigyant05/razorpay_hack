"""Thin CLI entry stub. No run logic yet (invariant: stubs only)."""

from __future__ import annotations

import argparse

from . import __version__
from .config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recovery-os", description="Recovery OS (foundation)")
    parser.add_argument("--version", action="version", version=f"recovery-os {__version__}")
    parser.add_argument("command", nargs="?", choices=["info"], default="info")
    args = parser.parse_args(argv)

    if args.command == "info":
        s = get_settings()
        print(f"recovery-os {__version__}")
        print(f"  provider     : {s.provider}")
        print(f"  db_path      : {s.db_path}")
        print(f"  seed         : {s.seed}")
        print(f"  max ceiling  : {s.max_auto_amount} paise")
        print("  run loop     : not implemented (phase 1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
