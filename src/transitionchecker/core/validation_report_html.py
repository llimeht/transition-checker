from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from html import escape
from pathlib import Path
from typing import cast


def default_html_report_path(json_report_path: Path) -> Path:
    """Return the default HTML report path beside a JSON validation report."""

    return json_report_path.with_suffix(".html")


def write_validation_report_html(report_path: Path, html_text: str) -> None:
    """Write rendered HTML report text to disk."""

    report_path.write_text(html_text, encoding="utf-8")


def render_validation_report_html(
    report: Mapping[str, object],
    *,
    suppressed_warning_codes: Collection[str],
    include_all_warnings: bool,
) -> str:
    """Render a self-contained HTML report from validation JSON-like data."""

    summary = _as_object_mapping(report.get("summary", {}))
    results = _as_object_mapping_list(report.get("results", []))

    grouped = _group_results_by_status(results)
    rendered_sections = [
        _render_section_component(
            "Failures",
            "failed",
            grouped["failed"],
            suppressed_warning_codes=suppressed_warning_codes,
            include_all_warnings=include_all_warnings,
        ),
        _render_section_component(
            "Accepted",
            "accepted",
            grouped["accepted"],
            suppressed_warning_codes=suppressed_warning_codes,
            include_all_warnings=include_all_warnings,
        ),
        _render_section_component(
            "Valid",
            "valid",
            grouped["valid"],
            suppressed_warning_codes=suppressed_warning_codes,
            include_all_warnings=include_all_warnings,
        ),
        _render_section_component(
            "Skipped Placeholder",
            "skipped_placeholder",
            grouped["skipped_placeholder"],
            suppressed_warning_codes=suppressed_warning_codes,
            include_all_warnings=include_all_warnings,
        ),
    ]

    sections_html = "\n".join(section for section in rendered_sections if section)
    summary_html = _render_summary_component(summary)
    nav_html = _render_nav_component(grouped)

    report_body = [summary_html, nav_html]
    if sections_html:
        report_body.append(sections_html)
    else:
        report_body.append(
            '<section class="card"><h2>No Validation Entries</h2><p>No results were available to render.</p></section>'
        )

    return _render_page_template(
        title="Plan Validation Report",
        excel_file=str(report.get("excel_file", "")),
        generated_at=str(report.get("generated_at_utc", "")),
        body="\n".join(report_body),
    )


def _render_page_template(
    *,
    title: str,
    excel_file: str,
    generated_at: str,
    body: str,
) -> str:
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{escape(title)}</title>
  <style>
    :root {{
      --bg: #f6f8fb;
      --surface: #ffffff;
      --text: #1f2937;
      --muted: #6b7280;
      --border: #dbe3ed;
      --ok: #1f7a1f;
      --warn: #9a6700;
      --bad: #b42318;
      --skip: #475467;
      --accent: #0b5cad;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "IBM Plex Sans", "Segoe UI", sans-serif; background: linear-gradient(180deg, #f8fafc 0%, #eef3f8 100%); color: var(--text); }}
    .container {{ max-width: 1200px; margin: 0 auto; padding: 1.25rem; }}
    header {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1rem; }}
    h1 {{ margin: 0 0 0.5rem 0; font-size: 1.4rem; }}
    .meta {{ color: var(--muted); font-size: 0.95rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; margin-top: 0.75rem; }}
    .stat {{ background: #fbfdff; border: 1px solid var(--border); border-radius: 10px; padding: 0.65rem 0.75rem; }}
    .stat .k {{ color: var(--muted); font-size: 0.82rem; }}
    .stat .v {{ font-weight: 700; font-size: 1.05rem; }}
    nav {{ margin: 0.9rem 0 1.25rem 0; }}
    nav a {{ margin-right: 0.55rem; text-decoration: none; color: var(--accent); font-weight: 600; font-size: 0.9rem; }}
    section {{ margin-bottom: 1rem; }}
    .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 0.9rem 1rem; margin-bottom: 0.75rem; }}
    .plan-head {{ display: flex; justify-content: space-between; align-items: baseline; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 0.4rem; }}
    .plan-name {{ font-weight: 700; }}
    .status {{ font-size: 0.82rem; font-weight: 700; border-radius: 999px; padding: 0.2rem 0.55rem; border: 1px solid transparent; }}
    .status.valid {{ color: var(--ok); border-color: #a7dca7; background: #effaf0; }}
    .status.accepted {{ color: var(--warn); border-color: #f2d18b; background: #fff9eb; }}
    .status.failed {{ color: var(--bad); border-color: #f0b0ab; background: #fff1f0; }}
    .status.skipped_placeholder {{ color: var(--skip); border-color: #d2d6db; background: #f7f8f9; }}
    .meta-row {{ color: var(--muted); font-size: 0.86rem; margin-bottom: 0.45rem; }}
    .block-title {{ font-size: 0.87rem; font-weight: 700; margin: 0.55rem 0 0.2rem; color: #344054; }}
    ul {{ margin: 0.25rem 0 0.45rem 1.15rem; }}
    li {{ margin-bottom: 0.18rem; }}
    .notes {{ border-left: 3px solid #c7d6e7; padding-left: 0.6rem; margin-top: 0.4rem; }}
    .muted {{ color: var(--muted); }}
  </style>
</head>
<body>
  <div class=\"container\">
    <header>
      <h1>{escape(title)}</h1>
      <div class=\"meta\">Workbook: {escape(excel_file) if excel_file else '(unknown)'}</div>
      <div class=\"meta\">Generated (UTC): {escape(generated_at) if generated_at else '(unknown)'}</div>
    </header>
    {body}
  </div>
</body>
</html>
"""


def _render_summary_component(summary: Mapping[str, object]) -> str:
    total = _as_int(summary.get("total_plan_files", 0))
    valid = _as_int(summary.get("valid", 0))
    accepted = _as_int(summary.get("accepted", 0))
    failed = _as_int(summary.get("failed", 0))
    skipped = _as_int(summary.get("skipped_placeholder", 0))

    return f"""
<section class=\"card\">
  <h2>Summary</h2>
  <div class=\"grid\">
    <div class=\"stat\"><div class=\"k\">Total</div><div class=\"v\">{total}</div></div>
    <div class=\"stat\"><div class=\"k\">Failed</div><div class=\"v\">{failed}</div></div>
    <div class=\"stat\"><div class=\"k\">Accepted</div><div class=\"v\">{accepted}</div></div>
    <div class=\"stat\"><div class=\"k\">Valid</div><div class=\"v\">{valid}</div></div>
    <div class=\"stat\"><div class=\"k\">Skipped</div><div class=\"v\">{skipped}</div></div>
  </div>
</section>
"""


def _render_nav_component(grouped: Mapping[str, list[Mapping[str, object]]]) -> str:
    links: list[str] = []
    for label, section_id in (
        ("Failures", "failed"),
        ("Accepted", "accepted"),
        ("Valid", "valid"),
        ("Skipped", "skipped_placeholder"),
    ):
        count = len(grouped.get(section_id, []))
        links.append(f'<a href="#{section_id}">{escape(label)} ({count})</a>')

    return f"<nav>{' '.join(links)}</nav>"


def _render_section_component(
    title: str,
    section_id: str,
    results: Sequence[Mapping[str, object]],
    *,
    suppressed_warning_codes: Collection[str],
    include_all_warnings: bool,
) -> str:
    if not results:
        return ""

    cards = "\n".join(
        _render_result_component(
            result,
            suppressed_warning_codes=suppressed_warning_codes,
            include_all_warnings=include_all_warnings,
        )
        for result in results
    )
    return f"""
<section id=\"{escape(section_id)}\">
  <h2>{escape(title)} ({len(results)})</h2>
  {cards}
</section>
"""


def _render_result_component(
    result: Mapping[str, object],
    *,
    suppressed_warning_codes: Collection[str],
    include_all_warnings: bool,
) -> str:
    status = str(result.get("status", ""))
    plan_file = str(result.get("plan_file", ""))
    rule_file = str(result.get("rule_file", ""))
    program_code = str(result.get("program_code", ""))

    findings = _as_object_mapping_list(result.get("findings", []))
    warnings = _filter_warnings(
        _as_object_mapping_list(result.get("warnings", [])),
        suppressed_warning_codes=suppressed_warning_codes,
        include_all=include_all_warnings,
    )
    offering_violations = _as_object_mapping_list(result.get("offering_violations", []))
    notes = _as_object_mapping(result.get("notes", {}))

    findings_html = _render_findings_component(findings)
    warnings_html = _render_warnings_component(warnings)
    offerings_html = _render_offerings_component(offering_violations)
    notes_html = _render_notes_component(notes)

    return f"""
<article class=\"card\">
  <div class=\"plan-head\">
    <div class=\"plan-name\">{escape(plan_file)}</div>
    <span class=\"status {escape(status)}\">{escape(status.upper() or 'UNKNOWN')}</span>
  </div>
  <div class=\"meta-row\">Program: {escape(program_code)} | Rule: {escape(rule_file)}</div>
  {findings_html}
  {offerings_html}
  {warnings_html}
  {notes_html}
</article>
"""


def _render_findings_component(findings: Sequence[Mapping[str, object]]) -> str:
    if not findings:
        return '<p class="muted">No findings.</p>'

    lines: list[str] = []
    for finding in findings:
        fid = str(finding.get("failure_id", "")).strip()
        msg = str(finding.get("message", "")).strip()
        accepted = bool(finding.get("accepted", False))
        prefix = "(accepted) " if accepted else ""
        if fid:
            lines.append(f"<li>{escape(prefix + '[' + fid + '] ' + msg)}</li>")
        else:
            lines.append(f"<li>{escape(prefix + msg)}</li>")
    return f"<div><div class=\"block-title\">Findings</div><ul>{''.join(lines)}</ul></div>"


def _render_warnings_component(warnings: Sequence[Mapping[str, object]]) -> str:
    if not warnings:
        return ""

    lines: list[str] = []
    for warning in warnings:
        code = str(warning.get("code", "")).strip()
        msg = str(warning.get("message", "")).strip()
        location = str(warning.get("location", "")).strip()
        display = f"[{code}] {msg}" if code else msg
        if location:
            display = f"{display} ({location})"
        lines.append(f"<li>{escape(display)}</li>")

    return f"<div><div class=\"block-title\">Warnings</div><ul>{''.join(lines)}</ul></div>"


def _render_offerings_component(violations: Sequence[Mapping[str, object]]) -> str:
    if not violations:
        return ""

    lines: list[str] = []
    for violation in violations:
        error_type = str(violation.get("error_type", "")).strip()
        if error_type == "offering_check_error":
            lines.append(f"<li>{escape(str(violation.get('message', 'offering check error')))}</li>")
            continue
        code = str(violation.get("course_code", "")).strip()
        planned = str(violation.get("planned_period", "")).strip()
        allowed = ", ".join(str(x) for x in _as_object_list(violation.get("allowed_periods", [])))
        if error_type == "period_not_allowed":
            text = f"{code}: planned {planned}; allowed {allowed or '(none)'}"
        elif error_type == "course_not_found":
            text = f"{code}: not found in offerings (planned {planned})"
        else:
            text = f"{code}: {error_type}"
        lines.append(f"<li>{escape(text)}</li>")

    return f"<div><div class=\"block-title\">Offering violations</div><ul>{''.join(lines)}</ul></div>"


def _render_notes_component(notes: Mapping[str, object]) -> str:
    graduate_outcome = str(notes.get("graduate_outcome", "")).strip()
    adjustment_type = str(notes.get("adjustment_type", "")).strip()
    for_reviewers = [str(x) for x in _as_object_list(notes.get("for_reviewers", []))]
    for_students = [str(x) for x in _as_object_list(notes.get("for_students", []))]

    if not (graduate_outcome or adjustment_type or for_reviewers or for_students):
        return ""

    reviewer_lines = "".join(f"<li>{escape(line)}</li>" for line in for_reviewers)
    student_lines = "".join(f"<li>{escape(line)}</li>" for line in for_students)

    return f"""
<div class=\"notes\">
  <div class=\"block-title\">Plan notes</div>
  <div>Graduate outcome: <strong>{escape(graduate_outcome or '(none)')}</strong></div>
  <div>Adjustment type: <strong>{escape(adjustment_type or '(none)')}</strong></div>
  {('<div class=\"block-title\">Notes for reviewers</div><ul>' + reviewer_lines + '</ul>') if reviewer_lines else ''}
  {('<div class=\"block-title\">Notes for students</div><ul>' + student_lines + '</ul>') if student_lines else ''}
</div>
"""


def _group_results_by_status(
    results: Sequence[Mapping[str, object]],
) -> dict[str, list[Mapping[str, object]]]:
    grouped: dict[str, list[Mapping[str, object]]] = {
        "failed": [],
        "accepted": [],
        "valid": [],
        "skipped_placeholder": [],
    }
    for result in results:
        status = str(result.get("status", "")).strip().lower()
        if status in grouped:
            grouped[status].append(result)
    return grouped


def _filter_warnings(
    warnings: Sequence[Mapping[str, object]],
    *,
    suppressed_warning_codes: Collection[str],
    include_all: bool,
) -> list[Mapping[str, object]]:
    if include_all:
        return list(warnings)

    suppressed = {code.strip() for code in suppressed_warning_codes if code.strip()}
    if not suppressed:
        return list(warnings)

    filtered: list[Mapping[str, object]] = []
    for warning in warnings:
        code = str(warning.get("code", "")).strip()
        if code in suppressed:
            continue
        filtered.append(warning)
    return filtered


def _as_object_mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, dict) else {}


def _as_object_list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _as_object_mapping_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    result: list[Mapping[str, object]] = []
    for item in cast(list[object], value):
        if isinstance(item, dict):
            result.append(cast(Mapping[str, object], item))
    return result


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return 0
    return 0
