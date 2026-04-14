from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, TypedDict, cast


CourseCode = str
RuleExpr = CourseCode | dict[str, Any]


class PlanCourseRecord(TypedDict, total=False):
    code: str


@dataclass
class RuleValidationError(Exception):
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def _is_course_code(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def normalize_clause(clause: Any) -> RuleExpr:
    """Normalize one requirement clause from canonical forms only."""
    if _is_course_code(clause):
        return clause.strip()

    if isinstance(clause, list):
        raise RuleValidationError(
            "<clause>",
            "legacy array clauses are no longer supported; use a course string or {'and'/'or': [...]}",
        )

    if isinstance(clause, dict):
        operator_clause = cast(dict[str, Any], clause)
        keys = set(operator_clause.keys())

        if keys == {"min", "from"}:
            min_count = operator_clause["min"]
            from_value = operator_clause["from"]
            if not isinstance(min_count, int) or min_count < 1:
                raise RuleValidationError("<clause>.min", "'min' must be a positive integer")
            if not isinstance(from_value, list):
                raise RuleValidationError("<clause>.from", "'from' must be an array")
            from_courses = cast(list[Any], from_value)
            if len(from_courses) < min_count:
                raise RuleValidationError(
                    "<clause>", f"'from' has {len(from_courses)} options but 'min' is {min_count}"
                )
            return {"min": min_count, "from": [normalize_clause(child) for child in from_courses]}

        if len(operator_clause) != 1:
            raise RuleValidationError("<clause>", "operator clause must contain exactly one key (or 'min' + 'from')")
        op = next(iter(operator_clause.keys()))
        if op not in ("and", "or"):
            raise RuleValidationError("<clause>", f"unsupported operator '{op}'")

        children_value = operator_clause[op]
        if not isinstance(children_value, list):
            raise RuleValidationError("<clause>", f"'{op}' value must be an array")
        children = cast(list[Any], children_value)
        if len(children) < 2:
            raise RuleValidationError("<clause>", f"'{op}' must contain at least 2 child expressions")

        return {op: [normalize_clause(child) for child in children]}

    raise RuleValidationError("<clause>", "clause must be a string or operator object")


def normalize_rules_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    Return a canonicalized copy of the full rules config.

    Canonical clause grammar:
    - leaf: "COURSE1234"
    - node: {"and": [expr, expr, ...]} or {"or": [expr, expr, ...]}
    """
    data = deepcopy(config)
    if "required" not in data or not isinstance(data["required"], dict):
        raise RuleValidationError("required", "config must contain an object field named 'required'")

    normalized_required: dict[str, list[RuleExpr]] = {}
    required_levels = cast(dict[str, Any], data["required"])
    for level_name, clauses in required_levels.items():
        if not isinstance(clauses, list):
            raise RuleValidationError(
                f"required.{level_name}", "level requirements must be an array of clauses"
            )
        normalized_required[level_name] = [normalize_clause(clause) for clause in cast(list[Any], clauses)]

    data["required"] = normalized_required
    data["schemaVersion"] = 2
    return data


def validate_canonical_expression(expr: RuleExpr, path: str = "<clause>") -> None:
    """Validate canonical expression shape (string leaves, and/or nodes, min/from nodes)."""
    if _is_course_code(expr):
        return

    if not isinstance(expr, dict):
        raise RuleValidationError(path, "expression must be a course string or operator object")

    keys = set(expr.keys())

    if keys == {"min", "from"}:
        min_count = expr["min"]
        from_value = expr["from"]
        if not isinstance(min_count, int) or min_count < 1:
            raise RuleValidationError(f"{path}.min", "'min' must be a positive integer")
        if not isinstance(from_value, list):
            raise RuleValidationError(f"{path}.from", "'from' must be an array")
        from_courses = cast(list[RuleExpr], from_value)
        if len(from_courses) < min_count:
            raise RuleValidationError(
                path, f"'from' has {len(from_courses)} options but 'min' is {min_count}"
            )
        for idx, child in enumerate(from_courses):
            validate_canonical_expression(child, f"{path}.from[{idx}]")
        return

    if len(keys) != 1:
        raise RuleValidationError(path, "operator object must contain exactly one key (or 'min' + 'from')")

    op = next(iter(keys))
    if op not in ("and", "or"):
        raise RuleValidationError(path, f"unknown operator '{op}'")

    children_value = expr[op]
    if not isinstance(children_value, list):
        raise RuleValidationError(path, f"'{op}' must be an array")
    children = cast(list[RuleExpr], children_value)
    if len(children) < 2:
        raise RuleValidationError(path, f"'{op}' must contain at least 2 child expressions")

    for idx, child in enumerate(children):
        validate_canonical_expression(child, f"{path}.{op}[{idx}]")


def evaluate_expression(expr: RuleExpr, completed_courses: Counter[str]) -> bool:
    """Evaluate canonical expression against a multiset of completed course codes."""
    if _is_course_code(expr):
        return expr in completed_courses

    if not isinstance(expr, dict):
        raise RuleValidationError("<eval>", "invalid expression shape")

    node = expr

    if set(node.keys()) == {"min", "from"}:
        min_count = cast(int, node["min"])
        from_exprs = cast(list[RuleExpr], node["from"])
        satisfied = sum(
            completed_courses[cast(str, child)] if _is_course_code(child)
            else int(evaluate_expression(child, completed_courses))
            for child in from_exprs
        )
        return satisfied >= min_count

    if len(node) != 1:
        raise RuleValidationError("<eval>", "invalid expression shape")

    op = next(iter(node.keys()))
    children = cast(list[RuleExpr], node[op])
    results = [evaluate_expression(child, completed_courses) for child in children]

    if op == "and":
        return all(results)
    if op == "or":
        return any(results)

    raise RuleValidationError("<eval>", f"unknown operator '{op}'")


def evaluate_level(level_clauses: list[RuleExpr], completed_courses: Counter[str]) -> bool:
    """Level semantics: all clauses in the level must evaluate to True."""
    return all(evaluate_expression(clause, completed_courses) for clause in level_clauses)


def evaluate_required(
    normalized_config: dict[str, Any],
    completed_courses: Counter[str],
) -> dict[str, bool]:
    """Return pass/fail per level under required-level AND semantics."""
    required = normalized_config.get("required", {})
    if not isinstance(required, dict):
        raise RuleValidationError("required", "required must be an object")

    result: dict[str, bool] = {}
    required_levels = cast(dict[str, Any], required)
    for level_name, clauses in required_levels.items():
        if not isinstance(clauses, list):
            raise RuleValidationError(
                f"required.{level_name}", "level requirements must be an array"
            )
        level_clauses = cast(list[RuleExpr], clauses)
        for idx, clause in enumerate(level_clauses):
            validate_canonical_expression(clause, f"required.{level_name}[{idx}]")
        result[level_name] = evaluate_level(level_clauses, completed_courses)

    return result


def validate_rules_config(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize and validate the full rules config; return canonical config."""
    normalized = normalize_rules_config(config)

    required = normalized.get("required", {})
    if not isinstance(required, dict):
        raise RuleValidationError("required", "required must be an object")

    for level_name, clauses in cast(dict[str, Any], required).items():
        if not isinstance(clauses, list):
            raise RuleValidationError(f"required.{level_name}", "level requirements must be an array")
        level_clauses = cast(list[RuleExpr], clauses)
        if len(level_clauses) == 0:
            raise RuleValidationError(f"required.{level_name}", "level must contain at least one clause")

        for idx, clause in enumerate(level_clauses):
            validate_canonical_expression(clause, f"required.{level_name}[{idx}]")

    return normalized


def expression_to_text(expr: RuleExpr, parent_op: str | None = None) -> str:
    """Render one canonical expression into a readable infix boolean string."""
    if _is_course_code(expr):
        return cast(str, expr)

    node = cast(dict[str, Any], expr)

    if set(node.keys()) == {"min", "from"}:
        min_count = cast(int, node["min"])
        from_exprs = cast(list[RuleExpr], node["from"])
        total = len(from_exprs)
        items = ", ".join(expression_to_text(child) for child in from_exprs)
        if min_count == total:
            return f"ALL OF ({items})"
        return f"AT LEAST {min_count} OF ({items})"

    op = next(iter(node.keys()))
    children = cast(list[RuleExpr], node[op])
    joiner = f" {op.upper()} "
    text = joiner.join(expression_to_text(child, op) for child in children)

    if parent_op is not None and parent_op != op:
        return f"({text})"
    return text


def render_rules_human(config: dict[str, Any]) -> str:
    """Render the validated rules config into a human-friendly multi-line summary."""
    lines: list[str] = []
    schema_version = config.get("schemaVersion", 2)
    lines.append(f"Degree rules (schemaVersion {schema_version})")

    required = cast(dict[str, list[RuleExpr]], config["required"])
    for level_name, clauses in required.items():
        lines.append("")
        lines.append(f"{level_name} ({len(clauses)} clauses)")
        for idx, clause in enumerate(clauses, start=1):
            lines.append(f"  {idx}. {expression_to_text(clause)}")

    return "\n".join(lines)


def diagnose_expression(expr: RuleExpr, completed_courses: Counter[str]) -> str:
    """Describe what is missing for a failed expression."""
    if _is_course_code(expr):
        return f"missing {expr}"

    node = cast(dict[str, Any], expr)

    if set(node.keys()) == {"min", "from"}:
        min_count = cast(int, node["min"])
        from_exprs = cast(list[RuleExpr], node["from"])
        satisfied = sum(
            completed_courses[cast(str, child)] if _is_course_code(child)
            else int(evaluate_expression(child, completed_courses))
            for child in from_exprs
        )
        needed = min_count - satisfied
        options = ", ".join(expression_to_text(e) for e in from_exprs)
        return f"need {needed} more from ({options})"

    op = next(iter(node.keys()))
    children = cast(list[RuleExpr], node[op])

    if op == "and":
        failed_diagnoses = [
            diagnose_expression(child, completed_courses)
            for child in children
            if not evaluate_expression(child, completed_courses)
        ]
        return "; ".join(failed_diagnoses)

    if op == "or":
        options = ", ".join(expression_to_text(child) for child in children)
        return f"none of the alternatives were completed: ({options})"

    return f"unknown operator '{op}'"


def report_plan(
    normalized_config: dict[str, Any],
    completed_courses: Counter[str],
) -> list[str]:
    """Return a list of human-readable failure strings for each unsatisfied clause."""
    failures: list[str] = []
    required = cast(dict[str, Any], normalized_config.get("required", {}))
    for level_name, clauses in required.items():
        level_clauses = cast(list[RuleExpr], clauses)
        for idx, clause in enumerate(level_clauses, start=1):
            if not evaluate_expression(clause, completed_courses):
                rule_text = expression_to_text(clause)
                diagnosis = diagnose_expression(clause, completed_courses)
                failures.append(f"[{level_name}] clause {idx}: {rule_text} \u2014 {diagnosis}")
    return failures


def extract_completed_courses(plan_data: dict[str, Any]) -> Counter[str]:
    courses_value = plan_data.get("courses")
    if not isinstance(courses_value, list):
        raise RuleValidationError("plan.courses", "plan JSON must contain a 'courses' array")

    completed_courses: Counter[str] = Counter()
    course_items = cast(list[Any], courses_value)
    for idx, course in enumerate(course_items):
        if not isinstance(course, dict):
            raise RuleValidationError(f"plan.courses[{idx}]", "course entry must be an object")

        course_record = cast(PlanCourseRecord, course)
        code = course_record.get("code")
        if isinstance(code, str) and code.strip():
            completed_courses[code] += 1

    return completed_courses


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and pretty-print degree rules JSON in canonical AND/OR/min-from grammar."
    )
    parser.add_argument("rules_file", help="Path to rules JSON file")
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="Print the validated canonical JSON after validation",
    )
    parser.add_argument(
        "--plan",
        metavar="PLAN_FILE",
        help="Path to a plan JSON file; report which degree rules the plan does not satisfy",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase logging verbosity (-v for INFO, -vv for DEBUG)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    # Configure logging based on verbosity
    if args.verbose >= 2:
        log_level = logging.DEBUG
    elif args.verbose >= 1:
        log_level = logging.INFO
    else:
        log_level = logging.WARNING

    logging.basicConfig(
        level=log_level,
        format="%(levelname)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    try:
        with open(args.rules_file, "r", encoding="utf-8") as handle:
            raw_config = json.load(handle)
    except FileNotFoundError:
        print(f"Error: file not found: {args.rules_file}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON: {exc}", file=sys.stderr)
        return 1

    if not isinstance(raw_config, dict):
        print("Error: top-level JSON must be an object", file=sys.stderr)
        return 1

    try:
        validated = validate_rules_config(cast(dict[str, Any], raw_config))
    except RuleValidationError as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 1

    logger.info("Rules validation: OK")
    logger.debug("Rules:\n" + render_rules_human(validated))

    if args.json_output:
        print("\nCanonical JSON")
        print(json.dumps(validated, indent=2, sort_keys=False))

    if args.plan:
        try:
            with open(args.plan, "r", encoding="utf-8") as fh:
                plan_data = json.load(fh)
        except FileNotFoundError:
            print(f"Error: plan file not found: {args.plan}", file=sys.stderr)
            return 1
        except json.JSONDecodeError as exc:
            print(f"Error: invalid plan JSON: {exc}", file=sys.stderr)
            return 1

        if not isinstance(plan_data, dict):
            print("Error: plan JSON must be an object with a 'courses' array", file=sys.stderr)
            return 1

        try:
            completed_courses = extract_completed_courses(cast(dict[str, Any], plan_data))
        except RuleValidationError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        failures = report_plan(validated, completed_courses)

        if failures:
            print(f"Plan does not satisfy {len(failures)} rule(s):")
            for failure in failures:
                print(f"  {failure}")
            return 1
        else:
            print("Plan satisfies all rules.")
    else:
        # Only show full rules output if not checking a plan or if verbose
        if args.verbose > 0:
            print()
            print(render_rules_human(validated))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())