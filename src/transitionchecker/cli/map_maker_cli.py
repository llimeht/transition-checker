"""Generate multiple candidate degree plans and export them to CSV.

The planner combines four inputs:
- degree rules: canonical requirement structure from ``degree_rules.py``
- offerings: which teaching periods each course can run in
- catalogue: course metadata such as UoC, level, and prerequisites
- intake template: the period layout and capacity model for an intake

Planning proceeds in four stages:
1. Choose the concrete set of required courses from the rules, resolving ``or``
    and ``min/from`` branches using feasibility plus optional steering hints.
2. Seed an initial assignment with a greedy placer.
3. Improve obvious defects with a local repair loop.
4. Explore alternatives with ruin-and-recreate, shift, and swap moves under a
    simulated annealing acceptance schedule.

The output is a CSV laid out by year/period, with one column block per solution.
Higher verbosity levels print restart summaries and search progress to stderr.

Examples:
  map-maker --rule rules/CEICDH3707-2026-2029.json --intake "2026 T1"
  map-maker --rule rules/CEICDH3707-2026-2029.json --intake "2026 T1" \
        --num-solutions 8 --restarts 20 --iterations 3000 \
        --output plans/CEIC/CEICDH3707_2026_T1_options.csv --verbose
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from transitionchecker.planner_engine import PlannerCommand, run_planner
from transitionchecker.utils.logging import configure_logging


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the planner entry point."""

    parser = argparse.ArgumentParser(
        description="Generate multiple candidate plans and export a period x option CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  map-maker --rule rules/CEICDH3707-2026-2029.json --intake "2026 T1"\n'
            '  map-maker --rule rules/CEICDH3707-2026-2029.json --intake "2026 T1" \\\n'
            "      --num-solutions 5 --output plans/CEIC/CEICDH3707_2026_T1_options.csv --verbose\n\n"
            "Notes:\n"
            "  --restarts controls how many independent baselines are explored.\n"
            "  --iterations controls the move budget per restart.\n"
            "  --patience can stop a restart early when no better plan is found.\n"
            "  --steering points to optional soft preferences such as course hints,\n"
            "      branch preferences, and soft precedence rules."
        ),
    )
    parser.add_argument("--rule", required=True, help="Path to degree rules JSON")
    parser.add_argument(
        "--intake",
        required=True,
        help=(
            "Intake in normalized YYYY pp format (e.g., '2026 T1'). "
            "Aliases like '2026 term 1' are accepted and normalized."
        ),
    )
    parser.add_argument(
        "--offerings",
        default="plans/offerings.json",
        help="Offerings JSON path (default: plans/offerings.json)",
    )
    parser.add_argument(
        "--catalogue",
        default="plans/catalogue.json",
        help="Catalogue JSON path (default: plans/catalogue.json)",
    )
    parser.add_argument(
        "--template-config",
        default="templates/template_configs.json",
        help="Template config JSON path (default: templates/template_configs.json)",
    )
    parser.add_argument(
        "--steering",
        default="templates/map_steering.json",
        help="Steering config JSON path (default: templates/map_steering.json)",
    )
    parser.add_argument(
        "--target-end",
        help=(
            "Optional boundary in the same syntax as --intake (e.g. '2028 S1') to discourage finishing after. "
            "Courses scheduled after the specified slot incur steering weight post_target_period_penalty. "
            "Format: YYYY period (e.g., 2028 S1, 2027 T3). "
            "If the target does not exist in the intake template, planner will exit with available valid targets."
        ),
    )
    parser.add_argument(
        "--partial-plan",
        help=(
            "Optional existing mapping-checker plan JSON used as a fixed partial map. "
            "Courses present in that plan are treated as fixed, and remaining capacity in "
            "those periods remains empty."
        ),
    )
    parser.add_argument("--num-solutions", type=int, default=5, help="Top K solutions")
    parser.add_argument(
        "--restarts", type=int, default=4, help="Independent SA restarts"
    )
    parser.add_argument(
        "--iterations", type=int, default=100, help="SA iterations per restart"
    )
    parser.add_argument(
        "--patience",
        type=int,
        default=None,
        help=(
            "Early-stop patience (iterations without improving best cost). "
            "Default: auto = max(5, iterations // 4)."
        ),
    )
    parser.add_argument(
        "--ruin-fraction",
        type=float,
        default=0.30,
        help="Fraction of courses to ruin in ruin-and-recreate moves",
    )
    parser.add_argument("--seed", type=int, default=1337, help="Random seed")
    parser.add_argument("--output", help="Output CSV path (stdout if omitted)")
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v summary, -vv iteration progress)",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)

    command = PlannerCommand(
        rule_path=Path(args.rule),
        intake=args.intake,
        offerings_path=Path(args.offerings),
        catalogue_path=Path(args.catalogue),
        template_config_path=Path(args.template_config),
        steering_path=Path(args.steering),
        target_end=args.target_end,
        partial_plan_path=Path(args.partial_plan) if args.partial_plan else None,
        num_solutions=args.num_solutions,
        restarts=args.restarts,
        iterations=args.iterations,
        patience=args.patience,
        ruin_fraction=args.ruin_fraction,
        seed=args.seed,
        output_path=Path(args.output) if args.output else None,
        verbose=args.verbose,
    )

    try:
        return run_planner(command, stdout=sys.stdout, stderr=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
