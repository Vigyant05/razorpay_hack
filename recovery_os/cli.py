"""Thin CLI entry."""

from __future__ import annotations

import argparse
import json

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
    p_run.add_argument("--provider", choices=["simulated", "razorpay_test"],
                       help="override the configured provider for this run")
    p_run.add_argument("--amount", type=int, default=5_000,
                       help="origination amount in paise, razorpay_test only (default 5000 = Rs 50)")
    p_run.add_argument("--trace", help="write this episode's full ledger trail to JSON")

    p_ask = sub.add_parser("ask", help="natural-language Q&A over the audit ledger")
    p_ask.add_argument("question")
    p_ask.add_argument("--db", default="batch.db", help="ledger db to query")

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

    if args.command == "ask":
        from .rag import LedgerNotFound, ask

        try:
            r = ask(args.question, db_path=args.db)
        except LedgerNotFound as e:
            print(f"{e}. Create a ledger to query first, e.g.:\n"
                  f"  recovery-os batch --n 50 --seed 42 --db {args.db}"
                  f"   (add --proposer llm for LLM diagnoses)")
            return 1
        print(r.answer)
        print(f"\n  matched {r.matched} episode(s)"
              + (f" · cited: {', '.join(r.cited_episode_ids[:8])}" if r.cited_episode_ids else "")
              + (" · [llm]" if r.used_llm else " · [deterministic]"))
        return 0

    if args.command == "run":
        from .orchestrator import run_episode
        from .providers import RazorpayTestProvider, get_provider

        provider = None
        if args.provider == "razorpay_test":
            provider = RazorpayTestProvider(amount=args.amount)  # amount is origination-only
        elif args.provider:
            provider = get_provider(args.provider)
        provider = provider or get_provider()  # build once; we print its name below

        diagnoser = proposer = None
        if args.proposer == "llm":
            from .llm import LLMProposer
            llm = LLMProposer(db_path=get_settings().db_path)
            diagnoser, proposer = llm.diagnose, llm.propose

        report = run_episode(args.payment_id, provider=provider,
                             diagnoser=diagnoser, proposer=proposer)
        print(f"episode      : {report.episode_id}")
        print(f"  provider   : {provider.name}")
        print(f"  cause      : {report.cause.value}")
        print(f"  action     : {report.intervention.value}")
        print(f"  policy     : {report.policy_status.value}"
              + (f" ({report.rule_fired})" if report.rule_fired else ""))
        print(f"  executed   : {report.executed.value if report.executed else '— (blocked)'}")
        print(f"  recovered  : {report.recovered}")
        if args.trace:
            from . import ledger
            rows = ledger.read(report.episode_id)
            trace = [{"step": r.step.value, "signature": r.signature,
                      "payload": json.loads(r.payload)} for r in rows]
            with open(args.trace, "w") as f:
                json.dump({"episode_id": report.episode_id,
                           "provider": provider.name,
                           "steps": trace}, f, indent=2)
            print(f"\n  wrote {args.trace} ({len(trace)} ledger rows)")
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
