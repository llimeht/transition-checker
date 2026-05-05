# UNSW 3+ to Flex-Semester transition-checker

Utilities for validating degree rules, extracting planning templates, and generating candidate degree plans.

The repository currently has three main workflows:

1. Extract and validate enrolment transition plans:
    - Extract enrolment and transition plans from standardised Excel sheets (`extract-plans`), noting the required semester offerings of courses.
    - Validate that plans will satisfy the degree rules and prerequisite rules with `degree-rules`
    - Validate that plans will satisfy actual intended semester offerings with `offering-checker`

2. Generate candidate enrolment transition plans based on degree rules and intended offerings.
    - Extract teaching period template and catalogue of offerings from standardised Excel sheets (`extract-template`)
    - Generate candidate enrolment plans with `map-maker`

3. Analyse enrolment sequences to plan clash free course combinations (CFCCs) to provide timetabling information (`cfcc-summary`)

## Requirements

- Python 3.11+
- Project dependencies installed either system-wide or in a venv; dependencies are documented in `pyproject.toml`
- Input files:
    - standardised transition planning spreadsheet (e.g. `CEIC Program Sequence Mapping.xlsx`, fetched from canonical location on SharePoint); stored in `plans/<SCHOOL>/`. These files are currently manually managed; we might change that some time soon.
    - degree rules for each specialisation of interest (e.g. `CEICAH3707.json`); stored in `rules/`; where rules have changed over time, they can be `<stream><program>-<YYYY>-<YYYY>.json` like `CEICDH3707-2020-2025.json` to indicate the start and stop handbook years. The degree rules are stored in a separate repository for ease of management.
    - offerings list in `plans/offerings.json`; this can be copied from the output of `extract-plans` with some manual checking that the courses are indeed in the intended teaching periods. Use `add-offerings` to maintain and normalise this file.

See [FILE-FORMATS.md](FILE-FORMATS.md) for examples of the JSON files used in `rules/` and `plans/`, including common formatting mistakes to avoid.


Example setup:

Obtain the source

```bash
git clone https://gitlab.cse.unsw.edu.au/SPrescott/transition-checker/
cd transition-checker
git clone https://gitlab.cse.unsw.edu.au/SPrescott/transition-checker-rules/ rules
```

Install the source into a Python virtual envrionment (will download the dependencies and create scripts)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

All the examples below assume that the package has been installed; the entry point scripts are used.



## Checking enrolment plans

### Perform validation of all plans in a spreadsheet

This will extract all plans from the spreadsheet and validate them against degree rules, prereq rules, and intended teaching period offerings.

```bash
plan-validate 'plans/CEIC/CEIC Program Sequence Mapping.xlsx'
```

To validate only a subset of exported plans, pass a glob that matches the plan filename stem:

```bash
plan-validate 'plans/CEIC/CEIC Program Sequence Mapping.xlsx' --filter 'CEICKS8338*'
```

### Check offering violations for a single plan

Validate that every course in a plan is offered in its scheduled teaching period.

```bash
offering-checker plans/CEIC/CEICDH3707_2026_T1.json
```

Looks for `offerings.json` in the same directory as the plan first; falls back to `plans/offerings.json` in the repository root. Exit code is 0 if no violations, 1 if violations found, 2 on input errors.

Use `--result-json` to get machine-readable output:

```bash
offering-checker plans/CEIC/CEICDH3707_2026_T1.json --result-json
```

### Validate the degree rules only

Validate the degree rules to make sure they appear to be syntactically correct.

```bash
degree-rules rules/CEICDH3707-2026-2029.json -v
```

### Validate a plan against rules and prerequisites

Validate an enrolment plan, checking the academic rules for the program+stream, and the prerequisite sequencing.

```bash
degree-rules \
    rules/TESTAH1234-2020-2025.json \
    --plan plans/TEST/TESTAH1234_2025_T2.json
```

If rule violations are found, these can be overridden so that the plan is ACCEPTED
(which is treated as a PASS) for all subsequent tests.
Each rule violation that is reported by `degree-rules` (or via `plan-validate`) includes
a short-code that describes the rule. For example:

```bash
degree-rules \
    rules/TESTAH1234-2020-2025.json \
    --plan plans/TEST/TESTAH1234_2025_T2.json
```
```
Plan has 1 prerequisite/corequisite violation(s):
  [prereq:TEST4000>96uoc] TEST4000 (2027 Term 1): missing 96uoc (has 84uoc)
```

And you can then make a permanent override of this error with `--add-override` if you've made
the academic decision that this is OK for the students, justifed for this transition,
and will be handled via advice/individual approvals, rather than a rule change in the handbook:

```bash
degree-rules \
    rules/TESTAH1234-2020-2025.json \
    --plan plans/TEST/TESTAH1234_2025_T2.json
    --add-override 'prereq:TEST4000>96uoc'
```
```
Plan status: ACCEPTED
```

The overrides are stored next to the plans (with `degree_rules_overrides` included in the filename)
and can be edited/deleted by hand.
The above override was created in `plans/TEST/TESTAH1234_2025_T2.degree_rules_overrides.json`


## Making enrolment plans

With degree rules, prereq information, and offerings, the tool actually has enough information
to try to make progression plans, including across the calendar transition.

### Generate one or more plan options

```bash
map-maker \
  --rule rules/CEICDH3707-2026-2029.json \
  --intake "2026 T1" \
  --num-solutions 4 \
  --restarts 12 \
  --iterations 200 \
  --show-nonstandard-periods \
  --output plans/CEIC/options.csv \
  -v
```

Copy whichever version of this plan you like back into the planning spreadsheet.
The `--show-nonstandard-periods` option includes summer and winter terms so that the rows should exactly match the spreadsheet format.

### Use steering hints to tune a plan

```bash
map-maker \
  --rule rules/CEICDH3707-2026-2029.json \
  --intake "2026 T1" \
  --steering templates/map_steering.json \
  --target-end "2029 S2" \
  --output /tmp/plan.csv \
  -v
```

`--target-end` is an optional indication of when the plan should try to ensure that a student
has completed the plan.
Accepts a full intake-style boundary (e.g., `"2027 Term 3"` or `"2028 S1"`).
The planner applies the steering weight `post_target_period_penalty` to each course scheduled
*after* that exact slot; setting this option will cause the planner to use Summer/Winter terms
rather than allowing the enrolment to spill into additional regular teaching periods.

### Use a partial plan as a basis for a full plan

Build the partial plan (say, for up to 2027 based on previously published enrolment sequences)
in the Excel file with the plans.

Export all the plans (including the partial plan):

```bash
extract-plans \
  --output-dir plans/CEIC/ \
  'plans/CEIC/CEIC Program Sequence Mapping.xlsx'
```

Complete the partial plan:

```bash
map-maker \
  --rule rules/CEICDH3707-2020-2025.json \
  --intake "2025 T3" \
  --steering templates/map_steering.json \
  --output /tmp/plan.csv \
  -v
```

Copy whichever version of this plan you like back into the spreadsheet.

### Steering configuration to tune `map-maker` behaviour

The optional steering file can influence plan shape without changing the rule set.

Typical uses:

- prefer a year or period for a course
- prefer one branch of an `or` clause
- encourage one course to appear before another

Example branch preference:

```json
{
  "branch_preferences": [
    {
      "courses": ["CHEM1811", "CHEM1821"],
      "weight": -50.0
    }
  ]
}
```

Interpretation:

- negative weight: prefer that branch
- positive weight: avoid that branch

### Search Tuning Notes

The most important planner controls are:

- `--restarts`: number of independent baseline attempts; restarts will fill the baseline plan in different ways based on some jitter parameters.
- `--iterations`: number of permitted course optimisation moves per restart
- `--patience`: early-stop threshold when a restart stops improving (defaults to 25% of the iterations)
- `--ruin-fraction`: how large ruin-and-recreate moves are

Practical guidance:

- increase `--restarts` when you want more diversity
- increase `--iterations` when each restart should search more deeply
- reduce `--patience` when long runs stall too often

### How `map-maker` Works

Planning is split into four stages:

1. Resolve the rules file into the concrete course set to schedule.
   - `or` and `min/from` clauses are resolved heuristically.
   - Steering can bias branch selection.
2. Build a baseline assignment with `greedy_place()`.
3. Improve obvious defects with `repair_assignments()`.
4. Explore alternatives with simulated annealing using:
   - ruin-and-recreate moves
   - shift moves
   - swap moves

The objective combines hard-leaning penalties and softer steering penalties, including:

- offering violations (i.e. course is not actually offered)
- prerequisite violations
- failed required clauses
- unplaced courses
- overload and seasonal penalties (i.e. avoid summer/winter)
- slot delay / compactness (i.e. prioritise graduating quickly)
- optional post-target penalty to discourage extending beyond a chosen end period
- course-level hints into a particular year, implicitly based on the first digit of the course code or explicitly via steering.
- soft precedence rules for preferred course sequencing


## Data, data sources, and data curation

### Manage the offerings list

Canonicalise and sort an offerings file in place:

```bash
add-offerings plans/offerings.json --validate
```

Add one or more teaching periods for a course (creates the entry if absent):

```bash
add-offerings plans/offerings.json --schedule CEIC2001 T1 T3
```

Periods are accepted in any alias form (`T1`, `term 1`, `S2`, `semester 2`, `summer`, etc.) and stored in canonical display form. Unknown period names cause a non-zero exit and leave the file unchanged.

An list of intended teaching periods can be exported, with an optional filter pattern
for which courses to invlude, and output either as plan text on the terminal or as CSV.

```bash
add-offerings plans/offerings.json --show '*'
add-offerings plans/offerings.json --show 'CEIC*'
add-offerings plans/offerings.json --show 'CEIC*' --output offerings.csv
```


### Override an prerequisite information in the catalogue

Some handbook prerequisite strings are so ambiguous or malformed that the parser cannot handle them at all.
Where the *intent* is clear enough to express as a valid prerequisite expression, you can add a catalogue override instead of trying to patch the parser.
You may also want to change prerequisite information in catalogue from what is currently approved based on changes that you know will be made.

Overrides are stored in `catalogue_overrides.json` beside `catalogue.json` and are merged in automatically whenever the catalogue is loaded by the validation tools.

Add (or update) an override for one course:

```bash
add-override plans/catalogue.json \
  --course TEST3000 \
  --career undergraduate \
  --prereq "Prerequisite: TEST2000. Corequisite: TEST2010" \
  --reason "Change TEST2010 to be a corequisite to make for easier sequencing."
```

The tool validates that the supplied `--prereq` text parses correctly before writing.
If the text cannot be parsed, the command exits with error (exits 1) and prints the parse error.
Use `--force` to write an unparseable override anyway (an auditing warning is printed).
The `--career` option will accept various aliases such as `UG` and `PG` as well.

The `date` field is stamped automatically with today's ISO date. The resulting file looks like (this is the same format as the `catalogue.json` file that the
`extract-templates` tool will generate):

```json
[
  {
    "code": "TEST3000",
    "career": "Undergraduate",
    "date": "2026-04-22",
    "prerequisites": "Prerequisite: TEST2000. Corequisite: TEST2010",
    "reason": "Change TEST2010 …"
  },
  {
    ...
  }
]
```

To remove an override, delete the relevant entry from `catalogue_overrides.json` by hand.

Note that the linter (`extract-template --lint`) always sees the raw handbook text even when overrides are present.

### Prerequisite Field Syntax

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
unsupported include:

 - Must be enrollmed in program 9999; Admission to program 9999.
 - Enrolment in a ABCD major
 - Must have completed at least XX UoC in program 9999; completed at least XX UoC of School of XYZ courses; completed at least XX UoC of ABCD (prefix) courses
 - Must have a WAM of XX or above
 - Only single and double degree School of XYZ students
 - This course is by application only
 - Enrolled in the final term of the program
 - Minimum mark of XX in ABCD1234
 - Must have completed XYZ test.

 Mixing these types of requirements in with an otherwise simple prerequisite expression might cause the entire field to be ignored.

 (We believe that some of these maturity requirements also cannot be implemented by UNSW's own systems.)


### Validating syntax and interpretation of prereq fields

There are two tools to use to look at the prereq parser performance

```bash
extract-template 'plans/CEIC/CEIC Program Sequence Mapping.xlsx' \
  --lint --lint-output catalogue-prereq-lint.json
```

Extract all prerequisite strings from the catalogue and the current parser result for each one.
This can be kept as a baseline and compared in future parser-change work.

```bash
extract-template 'plans/CEIC/CEIC Program Sequence Mapping.xlsx' \
  --prereq-snapshot-output plans/prereq-snapshot-baseline.json
```

The snapshot contains:

- metadata (source catalogue path, generation timestamp, entry count, parser marker)
- one entry per course with raw `prerequisites`, parsed `prereq_expr`, parsed `coreq_expr`, and parser `error`, and some `salvage` keys that show partial parser recovery information and classification.


### Obtain course metadata from the UNSW Handbook

This downloads handbook course pages, extracts the embedded JSON payload from each
page, and writes a CSV with career, title, offering terms, and prerequisite text.

```bash
import-handbook \
  --year 2026 \
  --career undergraduate \
  BIOC2101 CHEM1011 \
  --output plans/handbook_import.csv
```

The importer uses `requests` for fetching and currently targets
course handbook URLs of the form `https://www.handbook.unsw.edu.au/<career>/courses/<year>/<course>`.
Use `--career undergraduate` or `--career postgraduate` depending on which handbook path the course lives under.

(Note that the main spreadsheets now contain direct dumps of all fields from the STU054 report that should already contain this information and this tool is now of limited use.)


## Contributing

Please do! The code is type annotated and is clean with `mypy --strict` and `ruff format`.

The project is configured for strict mypy in `pyproject.toml`.

The project has a reasonable test coverage to help prevent regressions.
A test-driven approach to fixing bugs (and adding features!) is appreciated.
The test suite can be run using `python -m pytest`.

## Licence and credits

This work was developed by Stuart Prescott from the School of Chemical Engineering
as part of the UNSW 3+ to Flex-semester transition project.

Copyright 2026 UNSW Sydney

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS “AS IS” AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
