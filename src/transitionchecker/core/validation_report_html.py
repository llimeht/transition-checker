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


def render_validation_table_report_html(
        *,
        title: str,
        generated_at_utc: str,
        source_files: Sequence[str],
        rows: Sequence[Mapping[str, str]],
) -> str:
        """Render an interactive consolidated table report from flattened rows."""

        source_items = "".join(f"<li>{escape(item)}</li>" for item in source_files)
        source_html = (
                f"<details><summary>Source Files ({len(source_files)})</summary><ul>{source_items}</ul></details>"
                if source_items
                else ""
        )

        table_rows = "\n".join(_render_validation_table_row(row) for row in rows)
        if not table_rows:
                table_rows = (
                '<tr><td colspan="16" class="empty">No rows matched the selected inputs/filter.</td></tr>'
                )

        return f"""<!doctype html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>{escape(title)}</title>
    <link rel=\"stylesheet\" href=\"https://cdn.jsdelivr.net/npm/simple-datatables@9.0.3/dist/style.min.css\">
    <style>
        :root {{
            --bg: #f8fafc;
            --surface: #ffffff;
            --text: #13233a;
            --muted: #586475;
            --border: #d2dcea;
            --accent: #0f5ea8;
        }}
        * {{ box-sizing: border-box; }}
        body {{ margin: 0; background: radial-gradient(circle at top right, #eef6ff 0%, var(--bg) 40%); color: var(--text); font-family: \"IBM Plex Sans\", \"Segoe UI\", sans-serif; }}
        .container {{ max-width: 1920px; margin: 0 auto; padding: 0.6rem; }}
        .panel {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1rem; margin-bottom: 0.9rem; }}
        h1 {{ margin: 0 0 0.4rem 0; font-size: 1.4rem; }}
        .meta {{ color: var(--muted); margin-bottom: 0.4rem; font-size: 0.92rem; }}
        .status-note {{ color: var(--muted); font-size: 0.88rem; margin-top: 0.4rem; }}
        .column-controls {{ display: flex; flex-wrap: wrap; gap: 0.8rem 1rem; align-items: center; margin-top: 0.5rem; font-size: 0.9rem; }}
        .column-controls label {{ display: inline-flex; align-items: center; gap: 0.35rem; color: var(--muted); }}
        table {{ width: 100%; table-layout: fixed; }}
        th, td {{ padding: 0.35rem 0.42rem; font-size: 0.84rem; }}
        td {{ white-space: nowrap; }}
        th {{ white-space: normal; line-height: 1.15; vertical-align: bottom; }}
        td.wrap {{ white-space: normal; min-width: 0; }}
        .col-plan {{ width: 7rem; }}
        .col-plan-description {{ width: 12rem; }}
        .col-intake-year,
        .col-exit-year {{ width: 4.5rem; }}
        .col-intake-term,
        .col-exit-term {{ width: 4.8rem; }}
        .col-duration-years {{ width: 5.5rem; }}
        .col-validation-status,
        .col-impact-assessment {{ width: 8rem; }}
        .col-graduation-outcome,
        .col-adjustment {{ width: 10rem; }}
        .col-reviewer-notes,
        .col-student-notes {{ width: 11rem; }}
        .cell-elide-one,
        .cell-elide-two {{
            display: block;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .cell-elide-one {{ white-space: nowrap; }}
        .cell-elide-two {{
            white-space: normal;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            line-height: 1.2;
            max-height: 2.4em;
        }}
        .status-pill {{
            display: inline-flex;
            align-items: center;
            gap: 0.3rem;
            border-radius: 999px;
            border: 1px solid transparent;
            padding: 0.14rem 0.5rem;
            font-size: 0.79rem;
            font-weight: 700;
            letter-spacing: 0.01em;
            line-height: 1.1;
        }}
        .status-pill::before {{ font-size: 0.85rem; line-height: 1; }}

        .status-validation-ok {{ color: #14532d; background: #edf9f0; border-color: #9ed6ae; }}
        .status-validation-ok::before {{ content: "✓"; }}
        .status-validation-accepted {{ color: #9a6700; background: #fff8e8; border-color: #f2d18b; }}
        .status-validation-accepted::before {{ content: "⁉"; }}
        .status-validation-fail {{ color: #991b1b; background: #fff1f0; border-color: #efb2ad; }}
        .status-validation-fail::before {{ content: "✗"; }}
        .status-validation-unknown {{ color: #334155; background: #f4f6f8; border-color: #cfd8e3; }}
        .status-validation-unknown::before {{ content: "•"; }}

        .status-impact-complete {{ color: #0f5132; background: #eaf8f1; border-color: #9ad6be; }}
        .status-impact-complete::before {{ content: "◆"; }}
        .status-impact-pending {{ color: #7a4a00; background: #fff6e8; border-color: #edcb96; }}
        .status-impact-pending::before {{ content: "⌛"; }}
        .status-impact-unknown {{ color: #475569; background: #f6f8fa; border-color: #d6dde7; }}
        .status-impact-unknown::before {{ content: "○"; }}

        .findings-list {{ margin: 0; padding-left: 1.1rem; }}
        .findings-list li {{ margin-bottom: 0.2rem; }}
        .empty {{ text-align: center; color: var(--muted); padding: 1rem; }}
        .is-hidden-column {{ display: none !important; }}
        details {{ margin-top: 0.35rem; }}
        details ul {{ margin: 0.45rem 0 0 1.2rem; padding: 0; }}

        @media print {{
            body {{
                background: #ffffff !important;
            }}
            .container {{
                max-width: none;
                padding: 0;
            }}
            .panel {{
                border: 0;
                border-radius: 0;
                padding: 0;
                margin: 0 0 0.4rem 0;
            }}
            .column-controls,
            .status-note,
            .datatable-top,
            .datatable-bottom {{
                display: none !important;
            }}
            .datatable-container {{
                overflow: visible !important;
                max-height: none !important;
                height: auto !important;
            }}
            table {{
                table-layout: auto !important;
            }}
            #report-table th,
            #report-table td {{
                display: table-cell !important;
                white-space: normal !important;
                overflow: visible !important;
                text-overflow: clip !important;
            }}
            #report-table .cell-elide-one,
            #report-table .cell-elide-two {{
                display: block !important;
                white-space: normal !important;
                overflow: visible !important;
                text-overflow: clip !important;
                -webkit-line-clamp: unset !important;
                max-height: none !important;
            }}
        }}
    </style>
</head>
<body>
    <main class=\"container\">
        <section class=\"panel\">
            <h1>{escape(title)}</h1>
            <div class=\"meta\">Generated (UTC): {escape(generated_at_utc)}</div>
            <div class=\"meta\">Rows: {len(rows)}</div>
            {source_html}
            <div class=\"status-note\">Statuses are mapped to OK, FAIL, ACCEPTED. Placeholder-skipped plans are excluded.</div>
            <div class="column-controls">
                <strong>Columns</strong>
                <label><input type="checkbox" data-col-class="col-json-filename"> JSON filename</label>
                <label><input type="checkbox" data-col-class="col-plan-description"> Plan description</label>
                <label><input type="checkbox" data-col-class="col-cohort"> Cohort</label>
                <label><input type="checkbox" data-col-class="col-validation-findings"> Validation findings</label>
                <label><input type="checkbox" data-col-class="col-reviewer-notes"> Reviewer notes</label>
                <label><input type="checkbox" data-col-class="col-student-notes"> Student notes</label>
                <label><input type="checkbox" data-col-class="col-impact-assessment"> Impact Assessed</label>
            </div>
        </section>
        <section class=\"panel\">
            <table id=\"report-table\">
                <thead>
                    <tr>
                        <th class="col-json-filename">JSON<br>filename</th>
                        <th class="col-plan">Plan</th>
                        <th class="col-plan-description">Plan<br>description</th>
                        <th class="col-cohort">Cohort</th>
                        <th class="col-intake-year">Intake<br>year</th>
                        <th class="col-intake-term">Intake<br>term</th>
                        <th class="col-exit-year">Exit<br>year</th>
                        <th class="col-exit-term">Exit<br>term</th>
                        <th class="col-duration-years">Duration<br>(years)</th>
                        <th class="col-validation-findings">Validation<br>findings</th>
                        <th class="col-validation-status">Validation<br>status</th>
                        <th class="col-graduation-outcome">Graduation<br>outcome</th>
                        <th class="col-adjustment">Adjustment<br>type</th>
                        <th class="col-reviewer-notes">Reviewer<br>notes</th>
                        <th class="col-student-notes">Student<br>notes</th>
                        <th class="col-impact-assessment">Impact assessment<br>status</th>
                    </tr>
                </thead>
                <tbody>
{table_rows}
                </tbody>
            </table>
        </section>
    </main>
    <script src=\"https://cdn.jsdelivr.net/npm/simple-datatables@9.0.3\"></script>
    <script>
        const table = document.getElementById("report-table");
        const defaultHiddenColumns = new Set(["col-json-filename", "col-cohort", "col-validation-findings", "col-reviewer-notes", "col-student-notes", "col-impact-assessment"]);

        function applyColumnVisibility() {{
            if (!table) {{
                return;
            }}
            const checkboxes = document.querySelectorAll(".column-controls input[data-col-class]");
            checkboxes.forEach((checkbox) => {{
                const colClass = checkbox.getAttribute("data-col-class");
                if (!colClass) {{
                    return;
                }}
                const show = checkbox.checked;
                table.querySelectorAll("." + colClass).forEach((cell) => {{
                    cell.classList.toggle("is-hidden-column", !show);
                }});
            }});
        }}

        document.querySelectorAll(".column-controls input[data-col-class]").forEach((checkbox) => {{
            const colClass = checkbox.getAttribute("data-col-class");
            checkbox.checked = colClass ? !defaultHiddenColumns.has(colClass) : true;
            checkbox.addEventListener("change", applyColumnVisibility);
        }});

        if (table) {{
            const datatable = new simpleDatatables.DataTable(table, {{
                searchable: true,
                fixedHeight: true,
                perPage: 50,
                perPageSelect: [25, 50, 100, 250, 500],
                labels: {{
                    placeholder: "Search all columns...",
                }},
            }});

            applyColumnVisibility();

            if (typeof datatable.on === "function") {{
                ["datatable.page", "datatable.perpage", "datatable.update", "datatable.search", "datatable.sort"].forEach((eventName) => {{
                    datatable.on(eventName, applyColumnVisibility);
                }});
            }}

            const observer = new MutationObserver(() => applyColumnVisibility());
            observer.observe(table, {{ childList: true, subtree: true }});
        }}
    </script>
</body>
</html>
"""


def _render_validation_table_row(row: Mapping[str, str]) -> str:
    findings_html = row.get("validation_findings_html", "")
    findings_cell = (
        findings_html
        if findings_html and findings_html != "none"
        else escape(row.get("validation_findings", ""))
    )
    validation_status = str(row.get("validation_status", "") or "").strip().upper()
    impact_status = str(row.get("impact_assessment_status", "") or "").strip().upper()

    if validation_status == "OK":
        validation_class = "status-validation-ok"
    elif validation_status == "ACCEPTED":
        validation_class = "status-validation-accepted"
    elif validation_status == "FAIL":
        validation_class = "status-validation-fail"
    else:
        validation_class = "status-validation-unknown"

    if impact_status == "COMPLETE":
        impact_class = "status-impact-complete"
    elif impact_status == "PENDING":
        impact_class = "status-impact-pending"
    else:
        impact_class = "status-impact-unknown"

    validation_badge = (
        f"<span class=\"status-pill {validation_class}\">{escape(validation_status or 'UNKNOWN')}</span>"
    )
    impact_badge = (
        f"<span class=\"status-pill {impact_class}\">{escape(impact_status or 'UNKNOWN')}</span>"
    )

    plan_description = row.get("plan_description", "")
    graduation_outcome = row.get("graduation_outcome", "")
    adjustment_type = row.get("adjustment_type", "")
    reviewer_notes = row.get("reviewer_notes", "")
    student_notes = row.get("student_notes", "")

    plan_description_html = _render_elided_text(plan_description, lines=2)
    graduation_outcome_html = _render_elided_text(graduation_outcome, lines=2)
    adjustment_type_html = _render_elided_text(adjustment_type, lines=2)
    reviewer_notes_html = _render_elided_text(reviewer_notes, lines=1)
    student_notes_html = _render_elided_text(student_notes, lines=1)

    return (
        "          <tr>"
        f"<td class=\"col-json-filename\">{escape(row.get('json_filename', ''))}</td>"
        f"<td class=\"col-plan\">{escape(row.get('plan', ''))}</td>"
        f"<td class=\"wrap col-plan-description\">{plan_description_html}</td>"
        f"<td class=\"col-cohort\">{escape(row.get('cohort', ''))}</td>"
        f"<td class=\"col-intake-year\">{escape(row.get('intake_year', ''))}</td>"
        f"<td class=\"col-intake-term\">{escape(row.get('intake_term', ''))}</td>"
        f"<td class=\"col-exit-year\">{escape(row.get('exit_year', ''))}</td>"
        f"<td class=\"col-exit-term\">{escape(row.get('exit_term', ''))}</td>"
        f"<td class=\"col-duration-years\">{escape(row.get('duration_years', ''))}</td>"
        f"<td class=\"wrap col-validation-findings\">{findings_cell}</td>"
        f"<td class=\"col-validation-status\">{validation_badge}</td>"
        f"<td class=\"wrap col-graduation-outcome\">{graduation_outcome_html}</td>"
        f"<td class=\"wrap col-adjustment\">{adjustment_type_html}</td>"
        f"<td class=\"wrap col-reviewer-notes\">{reviewer_notes_html}</td>"
        f"<td class=\"wrap col-student-notes\">{student_notes_html}</td>"
        f"<td class=\"col-impact-assessment\">{impact_badge}</td>"
        "</tr>"
    )


def _render_elided_text(value: str, *, lines: int) -> str:
    text = value.strip()
    escaped_text = escape(text)
    class_name = "cell-elide-two" if lines > 1 else "cell-elide-one"
    return f'<span class="{class_name}" title="{escaped_text}">{escaped_text}</span>'


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
