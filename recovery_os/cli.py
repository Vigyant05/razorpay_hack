"""Thin CLI entry."""

from __future__ import annotations

import argparse

from . import __version__
from .config import get_settings, load_env_file


def main(argv: list[str] | None = None) -> int:
    load_env_file()  # pick up GROQ_API_KEY / ANTHROPIC_API_KEY etc. from a local .env
    parser = argparse.ArgumentParser(prog="recovery-os", description="Recovery OS")
    parser.add_argument("--version", action="version", version=f"recovery-os {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("info", help="print active config")
    p_run = sub.add_parser("run", help="run the recovery loop for one payment")
    p_run.add_argument("payment_id", help="e.g. pay_ABC123")
    p_run.add_argument("--proposer", default="heuristic", choices=["heuristic", "llm"],
                       help="diagnosis/proposal source (default heuristic)")

    p_batch = sub.add_parser("batch", help="run a seeded batch + scorecard")
    p_batch.add_argument("--n", type=int, default=100, help="number of episodes")
    p_batch.add_argument("--seed", type=int, default=42)
    p_batch.add_argument("--control-frac", type=float, default=0.2, dest="control_frac")
    p_batch.add_argument("--policy", default="agent",
                         choices=["agent", "immediate", "fixed_schedule", "never"])
    p_batch.add_argument("--proposer", default="heuristic", choices=["heuristic", "llm"],
                         help="agent proposer source (default heuristic)")
    p_batch.add_argument("--compare", action="store_true",
                         help="run all four policies side by side")
    p_batch.add_argument("--out", help="write the full scorecard(s) to JSON")
    p_batch.add_argument("--db", default="batch.db", help="ledger db for the batch")

    args = parser.parse_args(argv)

    if args.command == "batch":
        from .batch import run_batch, run_compare
        from .scorecard import to_comparison_table, to_table

        if args.compare:
            cmp = run_compare(args.n, args.seed, args.control_frac, db_path=args.db)
            print(to_comparison_table(cmp))
            for card in cmp.scorecards:
                print("\n" + to_table(card))
            payload = cmp
        else:
            card = run_batch(args.n, args.seed, args.control_frac, args.policy,
                             proposer_kind=args.proposer, db_path=args.db)
            print(to_table(card))
            payload = card

        if args.out:
            with open(args.out, "w") as f:
                f.write(payload.model_dump_json(indent=2))
            print(f"\nwrote {args.out}")
        return 0

    if args.command == "run":
        from .orchestrator import run_episode

        diagnoser = proposer = None
        if args.proposer == "llm":
            from .llm import LLMProposer
            llm = LLMProposer(db_path=get_settings().db_path)
            diagnoser, proposer = llm.diagnose, llm.propose

        report = run_episode(args.payment_id, diagnoser=diagnoser, proposer=proposer)
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
