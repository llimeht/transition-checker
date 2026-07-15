# File Formats

This page explains the files used by the transition checker.

The files are all in a format called JSON (pronounced "Jason").
If you have not worked with JSON before, that is fine. JSON is just a way of writing structured data as plain text. There's a "More about JSON files" section at the bottom
of this page that explains some of the syntax requirements and some common mistakes.

The file names are meaningful within the system too - the filenames given below are the default ones used by the tools.
They also encode program codes, specialisation codes, intakes etc.

## Before you edit anything

- Keep the filename exactly right.
- Keep the top-level shape exactly right.
- Use valid JSON with double quotes.
- Check commas carefully.
- If you can, run the relevant command after editing so the tool can catch mistakes quickly.

## 1. Degree rules files

Purpose:

- Defines the academic rules for a program or specialisation.
- Used by `degree-rules` and `map-maker`.

Path pattern:

```text
rules/<plan>.json
```

Top-level shape:

- Object

Important fields:

- `career`: usually `Undergraduate` or `Postgraduate`.
- `program`: object with `id` and `name`.
- `specialisations`: list of stream objects, each with `id` and `name`.
- `uoc`: total units of credit required.
- `validity`: handbook year range that these rules pertain to, with `from` and `to` entries to specify the years (inclusive).
- `required`: the course rule groups that must be satisfied.
- `rpl`: optional flat list of course codes implicitly treated as already held for prerequisite/corequisite checks only.

The program and specialisation details should be used to describe who these academic rules apply to. Ideally, they are the detaisl from the handbook as in the example below.

Copy-paste example:

```json
{
  "schemaVersion": 2,
  "career": "Undergraduate",
  "program": {
    "id": "3707",
    "name": "Bachelor of Engineering (Honours)"
  },
  "specialisations": [
    {
      "id": "CEICDH",
      "name": "Chemical Product Engineering"
    }
  ],
  "uoc": 192,
  "validity": {
    "from": "2026",
    "to": "2029"
  },
  "required": {
    "Level 1": [
      "ENGG1811",
      {
        "id": "MATH1A",
        "or": ["MATH1131", "MATH1141"]
      }
    ],
    "Electives": [
      {
        "min": 2,
        "placeholder": "CEICeeee",
        "from": ["CEIC6789", "CEIC8105"]
      }
    ]
  }
}
```

Notes:

- A plain string like `"ENGG1811"` means that exact course is required.
- An object with `"or"` means any one of those options can satisfy that requirement.
- An object with `"and"` means all of the courses are needed to satisfy that requirement (this is needed inside an `or` clause if the student has a list-A vs list-B choice)
- An object with `"min"` and `"from"` means a minimum number of choices from a list.
- The optional `"rpl"` list seeds prerequisite/corequisite validation with implicitly granted courses (such as magic-backend codes that appear via RPL), but those courses do not count as satisfying `"required"` clauses unless they also appear in the student's actual completed or planned course history.
- The name of each object inside `"required"` is informational only, but matching it against the handbook helps with clarity.
- In groups of electives as illustrated above, and in simple `"or"` clauses, the `"placeholder"` course code can be used in enrolment plans as a generic pseudo-course-code rather than specifying exact courses.
- These files are usually edited carefully by hand - once created, they don't often need to be changed. If you do edit them by hand, be extra careful with brackets and commas.

## 2. Exported plan files

Purpose:

- Stores a single enrolment plan showing how a student progresses from admission to graduation through required courses, allocated to each term (extracted from a spreadsheet).
- Used by `degree-rules`, `offering-checker`, and other validation tools.

Path pattern:

```text
plans/<school>/<plan>_<intake>.json
```

Example path:

```text
plans/CEIC/CEICDH3707_2026_T1.json
```

Top-level shape:

- Object

Important fields:

- `sheet`: spreadsheet tab name.
- `intake`: intake label in the format `YYYY PP` for the 4 digit year and the 2 character teaching period abbreviation, such as `T1` or `S2`.
- `program`: program/specialisation/plan code as needed to identify the plans.
- `career`: career of the program, used for course catalogue lookups. Vertical double degrees such as the BE(Hons) MBiomedE have `"Undergraduate"` as the career throughout.
- `uoc`: total plan UOC.
- `courses`: list of planned courses.

Each item in `courses` is an object with fields:

- `enrol_year`: the enrolment year level (`"Year 1"`, `"Year 2"`, ...)
- `year`: the calendar year (2025, ...)
- `period`: the teaching period (`"Term 1"`, `"Semester 2"`, `"Summer Term"`)
- `course_n`: which course this is in the term - this is an unimportant indexing detail from the spreadsheet
- `code`: course code
- `title`: course name
- `uoc`: Units of Credit for the course
- `prerequisites`: plain text rendering of the spreadsheets from the STU054 from report, ideally.

Copy-paste example:

```json
{
  "sheet": "TEST",
  "intake": "2024 T1",
  "program": "3707",
  "career": "Undergraduate",
  "uoc": 192,
  "courses": [
    {
      "enrol_year": "Year 1",
      "year": 2024,
      "period": "Term 1",
      "course_n": "Course 1",
      "code": "TEST1001",
      "title": "Intro to Testing",
      "uoc": 6,
      "prerequisites": "."
    },
    {
      "enrol_year": "Year 1",
      "year": 2024,
      "period": "Term 2",
      "course_n": "Course 2",
      "code": "TEST2001",
      "title": "Advanced Testing A",
      "uoc": 6,
      "prerequisites": "TEST1001"
    }
  ]
}
```

Notes:

- The file must stay as one object with a `courses` list inside it.
- Do not turn it into a top-level list.
- When validation is run with a catalogue, the catalogue can override the `prerequisites`. In other words, the plan file is not always the final source of truth for course information; see details on making local overrides below.

## 3. Degree-rule override sidecar files

Purpose:

- Stores accepted exceptions for a specific plan: when academic judgement has developed a policy decision to violate the degree rules for a group of students, such as waiving a prereq.
- Created by `degree-rules --add-override`.

Path pattern:

```text
plans/<school>/<plan>_<intake>.degree_rules_overrides.json
```

Example path:

```text
plans/CEIC/CEICDH3707_2025_T2.degree_rules_overrides.json
```

Top-level shape:

- Object

Important fields:

- `overrides`: list of override entries.
- `failure_id`: the short code of the failure being accepted - the checking tools will specify the short code for each failure that is found.
- `added_at_utc`: optional timestamp written by the tool.

Copy-paste example:

```json
{
  "overrides": [
    {
      "failure_id": "prereq:CEICEEEE>96uoc",
      "added_at_utc": "2026-04-20T01:50:38.725055+00:00"
    }
  ]
}
```

Important warning:

- When asked to process `<plan>_<intake>.json`, the tool looks for `<plan>_<intake>.degree_rules_overrides.json`.
- If the filename is wrong, the override file will be ignored.
- To remove overrides, edit the file by hand.

## 4. Course equivalence files

Purpose:

- Declares that holding one course also counts as holding another course, for the purposes of degree-rule and prerequisite checking.
- Useful when courses have been renamed, replaced, or a school-local pseudo-course code (e.g. `DESN2000CEIC`) should satisfy rules written in terms of a standard code (e.g. `DESN2000`).
- Does **not** affect the catalogue, plan data, or UoC counts — equivalences are applied only at expression evaluation time.

Path patterns (both optional; both are loaded and their lists concatenated — they are additive):

```text
plans/degree_rules_equivalences.json
plans/<school>/degree_rules_equivalences.json
```

Top-level shape:

- List

Important fields in each list item:

- `held`: required; course code (or pseudo-code) that the student holds in their plan.
- `equivalent_to`: required; code that `held` is treated as during evaluation.
- `reason`: optional note for humans.

The mapping is **directional** — `held → equivalent_to`.  For symmetric equivalence, add two entries.

Copy-paste example:

```json
[
  {
    "held": "DESN2000CEIC",
    "equivalent_to": "DESN2000",
    "reason": "CEIC local variant of DESN2000; counts toward same rules"
  },
  {
    "held": "DESN1000",
    "equivalent_to": "ENGG1000",
    "reason": "DESN1000 replaced ENGG1000 in rules"
  }
]
```

Important warnings:

- `held` and `equivalent_to` values are normalized to uppercase on load; `"desn2000ceic"` and `"DESN2000CEIC"` are treated identically.
- Pseudo-codes (codes that do not match the standard `[A-Z]{4}[0-9]{4}` pattern) are fully supported.
- Entries with missing or empty `held` / `equivalent_to` are skipped and a validation warning is emitted.
- To remove an equivalence, delete the entry by hand.

## 5. Catalogue override files

Purpose:

- Overrides catalogue course data, especially prerequisites.
- Useful when the handbook text is unclear, wrong, or intentionally being adjusted for planning.

This file supplements the course catalogue (in `catalogue.json`) that is extracted automatically from the planning spreadsheet and contains the Handbook data via STU054; the `catalogue.json` file should not be edited directly, with any incorrect or missing data corrected via this overrides file.

Path patterns:

```text
plans/catalogue_overrides.json
plans/<school>/catalogue_overrides.json
```

Top-level shape:

- List

Important fields in each list item:

- `code`: the course code or placeholder pseudo-course-code for electives.
- `career`: required; `"Undergraduate"` or `"Postgraduate"`.
- `title`: optional but often helpful.
- `uoc`: optional.
- `prerequisites`: optional but common.
- `reason`: optional note for humans.
- `date`: optional date for humans.

Copy-paste example:

```json
[
  {
    "code": "CEIC2002",
    "career": "Undergraduate",
    "prerequisites": "Corequisite: CEIC2001",
    "reason": "Allow CEIC2001 and CEIC2002 to be scheduled together",
    "date": "2026-04-22"
  }
]
```

Important warnings:

- This file starts with `[` because it is a list, not an object.
- Do not try to write it as `{ "CEIC2002": ... }`.
- `code` and `career` are the important matching fields. If they are wrong or missing, the override will not match the intended course.
- If both contain an override for the same `code` and `career`, the school-local file wins.
- Rather than editing by hand, you can use the `add-overrides` tool.

## 6. Prerequisite (ERG) expression files

The `erg_expr` stored per course is a JSON tree with typed leaf nodes:

| Leaf                                        | Meaning                                          |
|---------------------------------------------|--------------------------------------------------|
| `{"prereq": "CEIC3004"}`                    | Course must be completed before                  |
| `{"coreq": "CEIC3007"}`                     | Course may be taken in the same term             |
| `{"prereq_pattern": "JURD####"}`            | Any JURD course at any level; # is any digit     |
| `{"uoc": 48}`                               | Total prior UoC ≥ 48                             |
| `{"uoc": 72, "restriction": "JURD####"}`    | ≥ 72 UoC from matching courses                   |
| `{"condition": "Enrolment in ..."}`         | Enrolment restriction — always passes validation |

Combined with `{"and": [...]}` / `{"or": [...]}` at any nesting depth.

For entries where the ERG data cannot be fully parsed (e.g. complex external requirement
table references), the tool falls back to the human-readable ERG Description text which is
then processed by the standard `parse_prerequisite_field` parser.

## 7. Hand-written Prerequisite Field Syntax (for Overrides)

Plan and catalogue `prerequisites` fields are parsed with a strict token-based
grammar in the rules engine. There are lots of quite interesting things written in the
handbook that cannot be supported by this tool and it can only possibly implement
course-level constraints, not constraints based on program, specialisation, or marks in other courses.

Supported syntax is:

- course code tokens matching `[A-Z]{4}[A-Z0-9]*(?:-[A-Z0-9]+)?` (i.e. `ABC1234` but also some variations as needed like `ABC1234-special`, or `ABCDES-RPL`)
- UOC tokens like `120 UOC` (case-insensitive), interpreted as minimum UoC required to take a course
- boolean operators `AND` and `OR` (case-insensitive).
- parentheses for grouping
- `PLUS` between clauses; each `PLUS` segment is combined as `AND`
- co-requisite split markers: `COREQ...` or `CO-REQ...` (with optional `:`)

Operator precedence is `AND` before `OR`.

Normalisation rules applied before parsing:

- `&` and `,` are treated as `AND`
- `;` and `.` are treated as separators between separate sets of conditions.
- `COMPLETION OF` is ignored
- blank values, `.`, and `0` are treated as no prerequisite

Examples:

- `CEIC2001, CEIC2002`  (equivalent to `CEIC2001 AND CEIC2002`)
- `CEIC2005 AND (CEIC3004 OR CHEM2021)`
- `CEIC2001 PLUS COMPLETION OF 96 UOC` (completion of CEIC2001 and a maturity rule of 96 UoC completed)
- `MATH1231. COREQ: PHYS1121` (prereq on MATH1231 and a coreq on PHYS1121)

Any prerequisite text that cannot be tokenised with this grammar is reported
as an unsupported prerequisite in validation output; it is skipped when trying
to apply the rules. Requirements that exist in the handbook that are known to be
unsupported in the *text parser* include:

- Must be enrolled in program 9999; Admission to program 9999.
- Enrolment in a ABCD major
- Must have completed at least XX UoC in program 9999; completed at least XX UoC of School of XYZ courses; completed at least XX UoC of ABCD (prefix) courses
- Must have a WAM of XX or above
- Only single and double degree School of XYZ students
- This course is by application only
- Enrolled in the final term of the program
- Minimum mark of XX in ABCD1234
- Must have completed XYZ test.

Many of these are handled correctly when the ERG structured data is used instead
(see **Import structured prerequisite data from the ERG report** above).
In particular, complex groupings, mixed prereq/coreq, maturity requirements,
programme enrolment conditions, and course-pattern restrictions are all supported
in the ERG expression tree.

 Mixing these types of requirements in with an otherwise simple prerequisite expression might cause the entire field to be ignored.

 (We believe that some of these maturity requirements also cannot be implemented by UNSW's own systems.)

### Validating syntax and interpretation of prereq fields

There are two tools to use to look at the prereq parser performance

```bash
extract-template plans/CEIC/CEIC_Sequences.xlsx \
  --lint --lint-output catalogue-prereq-lint.json
```

Extract all prerequisite strings from the catalogue and the current parser result for each one.
This can be kept as a baseline and compared in future parser-change work.

```bash
extract-template plans/CEIC/CEIC_Sequences.xlsx \
  --prereq-snapshot-output plans/prereq-snapshot-baseline.json
```

The snapshot contains:

- metadata (source catalogue path, generation timestamp, entry count, parser marker)
- one entry per course with raw `prerequisites`, parsed `prereq_expr`, parsed `coreq_expr`, and parser `error`, and some `salvage` keys that show partial parser recovery information and classification.

See also the linting scripts in the `tools` directory.

## 7. Offerings file

Purpose:

- Lists which teaching periods each course is offered in.
- Used by `offering-checker`, `map-maker`, and related tools.

Path:

```text
plans/offerings.json
```

Top-level shape:

- Object

Format:

- Each key is a course code.
- Each value may be either:
  - a list of canonical period names, meaning the course is offered in those periods in all years, or
  - an object whose keys are calendar years and whose values are lists of canonical period names.
- A year-aware object may also include an `all` key. That key acts as the fallback all-years list when a tool needs offerings for a year that is not explicitly listed.

Copy-paste example:

```json
{
  "CEIC2000": ["Term 1", "Semester 1"],
  "CEIC2001": ["Term 1", "Semester 1"],
  "CEIC4000": ["Term 2", "Term 3", "Semester 1", "Semester 2"]
}
```

Year-aware example:

```json
{
  "CEIC2001": {
    "all": ["Term 2"],
    "2026": ["Term 1"],
    "2027": ["Term 3"]
  },
  "CEIC4000": {
    "2026": ["Semester 1"],
    "2027": ["Semester 2"]
  }
}
```

Important warnings:

- This file is an object, not a list.
- The values must be either lists or year-keyed objects whose inner values are lists.
- Period names should use the normal display form written by the tools, such as `Term 1`, `Term 2`, `Term 3`, `Semester 1`, `Semester 2`, `Summer Term`, and `Winter Term`.
- If a year-aware course has no entry for the year being checked, the tools fall back to that course's `all` list when it exists.
- If you are editing this file by hand, it is easy to miss a comma between course entries because the file can get long.
- Rather than editing by hand, you can use the `add-offerings` tool.

## Quick checklist

Before you save, check these five things:

1. Is the filename exactly right? (that means file extensions, including on operating systems that try to hide file extensions from you, like Microsoft Windows)
2. Does the file start with the right top-level shape, either `{` or `[`?
3. Are all text values in double quotes?
4. Are commas present between items, but not after the last item?
5. Have you run a command to check the file?

## Which command should I run after editing?

- After editing `rules/<plan>.json`, run `degree-rules <that rule> -v`.
- After editing `plans/<school>/<plan>_<intake>.json`, run `degree-rules ... --plan <that plan>` or `offering-checker <that file>` depending on what you changed.
- After editing a degree-rule override sidecar, run `degree-rules ... --plan <that plan>` again to confirm the override is being picked up.
- After editing `plans/catalogue_overrides.json` or `plans/<school>/catalogue_overrides.json`, run the relevant validation command again.
- After editing `plans/offerings.json`, run `add-offerings plans/offerings.json --validate` or `offering-checker <plan file>`.

## More about JSON files

- Use `{}` for a name-value pairs object, which is a collection of named fields.
- Use `[]` for a list, which is a collection of items.
- Put text in double quotes, like `"CEIC2001"`.
- Put commas between items.
- Do not put a comma after the last item in an object or list.
- JSON does not allow comments, so avoid adding lines like `// note` or `# note`.

## Common JSON mistakes

These are the most common reasons a file stops working after a hand edit.

### Missing comma between items

Bad:

```json
{
  "code": "CEIC2001"
  "career": "Undergraduate"
}
```

Good:

```json
{
  "code": "CEIC2001",
  "career": "Undergraduate"
}
```

### Trailing comma after the last item

Bad:

```json
{
  "code": "CEIC2001",
  "career": "Undergraduate",
}
```

Good:

```json
{
  "code": "CEIC2001",
  "career": "Undergraduate"
}
```

### Single quotes instead of double quotes

Bad:

```json
{
  'code': 'CEIC2001'
}
```

Good:

```json
{
  "code": "CEIC2001"
}
```

### Wrong top-level shape

Some files start with a name-value pair object (`{ ... }`), because the file is about a
collection of definitions (values) each one having a name pointing at it,
such as when each course will be offered in the calendar.
The name in the name-value pair must be unique, so this can be used for properties of
a course such as "when is it offered", but cannot be used if there are multiple separate
records for a course because it exists in multiple careers with different metadata.

Other files start with a list (`[ ... ]`), because the file is a list of definitions,
such as the course catalogue which is a list of all course metadata; due to courses
existing with different metadata in different careers (same course code but different prereq information, for instance), the course code is not unique in this situation.

If the wrong shape is used, the tool cannot load the file even if the JSON is otherwise valid.

Bad for `plans/offerings.json`:

```json
[
  {
    "code": "CEIC2001",
    "periods": ["Term 1"]
  }
]
```

Good for `plans/offerings.json`:

```json
{
  "CEIC2001": ["Term 1"]
}
```
