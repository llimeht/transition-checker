from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, cast


CourseCode = str
RuleExpr = CourseCode | dict[str, Any]


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
        if len(operator_clause) != 1:
            raise RuleValidationError("<clause>", "operator clause must contain exactly one key")
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
    """Validate canonical expression shape (string leaves + and/or nodes)."""
    if _is_course_code(expr):
        return

    if not isinstance(expr, dict):
        raise RuleValidationError(path, "expression must be a course string or operator object")
    if len(expr) != 1:
        raise RuleValidationError(path, "operator object must contain exactly one key")

    op = next(iter(expr.keys()))
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


def evaluate_expression(expr: RuleExpr, completed_courses: set[str]) -> bool:
    """Evaluate canonical expression against a set of completed course codes."""
    if _is_course_code(expr):
        return expr in completed_courses

    if not isinstance(expr, dict) or len(expr) != 1:
        raise RuleValidationError("<eval>", "invalid expression shape")

    op = next(iter(expr.keys()))
    children = expr[op]
    results = [evaluate_expression(child, completed_courses) for child in children]

    if op == "and":
        return all(results)
    if op == "or":
        return any(results)

    raise RuleValidationError("<eval>", f"unknown operator '{op}'")


def evaluate_level(level_clauses: list[RuleExpr], completed_courses: set[str]) -> bool:
    """Level semantics: all clauses in the level must evaluate to True."""
    return all(evaluate_expression(clause, completed_courses) for clause in level_clauses)


def evaluate_required(
    normalized_config: dict[str, Any],
    completed_courses: set[str],
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


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and pretty-print degree rules JSON in canonical AND/OR grammar."
    )
    parser.add_argument("rules_file", help="Path to rules JSON file")
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="Print the validated canonical JSON after validation",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

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

    print("Validation: OK")
    print(render_rules_human(validated))

    if args.json_output:
        print()
        print("Canonical JSON")
        print(json.dumps(validated, indent=2, sort_keys=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())