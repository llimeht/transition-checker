from __future__ import annotations

import argparse
import sys
from pathlib import Path

from transitionchecker.rules_engine import RulesCommand, run_rules_command
from transitionchecker.utils.logging import configure_logging


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate degree rules JSON and optionally validate a plan against them.",
        epilog=(
            "Examples:\n"
            "  degree_rules.py rules/CEICAH3707.json -v\n"
            "  degree_rules.py rules/CEICAH3707.json --plan plans/CEIC/CEICAH3707_2026_T1.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("rules_file", help="Path to degree rules JSON file")
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="Print the validated canonical rules JSON",
    )
    parser.add_argument(
        "--plan",
        metavar="PLAN_FILE",
        help=(
            "Path to a plan JSON file to validate against the supplied rules "
            "and prerequisite/corequisite checks"
        ),
    )
    parser.add_argument(
        "--plan-report-json",
        action="store_true",
        help="With --plan, print machine-readable validation JSON",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase logging verbosity (-v for INFO, -vv for DEBUG)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    configure_logging(args.verbose)
    command = RulesCommand(
        rules_file=Path(args.rules_file),
        json_output=args.json_output,
        plan_file=Path(args.plan) if args.plan else None,
        plan_report_json=args.plan_report_json,
        render_rules_text=args.plan is None and args.verbose > 0,
    )
    return run_rules_command(command, stdout=sys.stdout, stderr=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
