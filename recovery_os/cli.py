"""Thin CLI entry stub. No run logic yet (invariant: stubs only)."""

from __future__ import annotations

import argparse

from . import __version__
from .config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recovery-os", description="Recovery OS")
    parser.add_argument("--version", action="version", version=f"recovery-os {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("info", help="print active config")
    p_run = sub.add_parser("run", help="run the recovery loop for one payment")
    p_run.add_argument("payment_id", help="e.g. pay_ABC123")
    args = parser.parse_args(argv)

    if args.command == "run":
        from .orchestrator import run_episode

        report = run_episode(args.payment_id)
        print(f"episode      : {report.episode_id}")
        print(f"  cause      : {report.cause.value}")
        print(f"  action     : {report.intervention.value}")
        print(f"  policy     : {report.policy_status.value}"
              + (f" ({report.rule_fired})" if report.rule_fired else ""))
        print(f"  executed   : {report.executed.value if report.executed else '— (blocked)'}")
        print(f"  recovered  : {report.recovered}")
        return 0

    # default: info
    s = get_settings()
    print(f"recovery-os {__version__}")
    print(f"  provider     : {s.provider}")
    print(f"  db_path      : {s.db_path}")
    print(f"  seed         : {s.seed}")
    print(f"  max ceiling  : {s.max_auto_amount} paise")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
