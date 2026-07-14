# Unofficial UNSW 3+ to Flex-Semester transition-checker

Utilities for validating degree rules, extracting planning templates, and generating candidate degree plans.

These utilities are entirely unofficial and are designed to help staff plan enrolment sequences.
They do not replace careful consideration of the academic rules in the handbook, checking the intended
offerings for courses in each teaching period, or academic judgement about what allowances might be made
to help with the transition.

The repository currently has three main workflows:

1. Extract and validate enrolment transition plans:
    - Extract enrolment and transition plans from standardised Excel sheets (`extract-plans`), noting the required semester offerings of courses.
    - Validate that plans will satisfy the degree rules and prerequisite rules, and that the plan does not require overloading within the calendar year (i.e. more than 48 UoC in a calendar year) with `degree-rules`
    - Validate that plans will satisfy actual intended semester offerings with `offering-checker`

2. Generate candidate enrolment transition plans based on degree rules and intended offerings.
    - Extract teaching period template and catalogue of offerings from standardised Excel sheets (`extract-template`)
    - Generate candidate enrolment plans with `map-maker`

3. Analyse enrolment sequences to plan clash free course combinations (CFCCs) to provide timetabling information (`cfcc-summary`)

Users of these tools might also be interested in
[Sequence Visualiser](https://github.com/llimeht/sequence-visualiser)
for turning the sequence outputs from this tool into HTML or PDF enrolment plans.

## Requirements

- Python 3.11+
- Project dependencies installed either system-wide or in a venv; dependencies are documented in `pyproject.toml`
- Input files:
  - standardised transition planning spreadsheet (e.g. `CEIC_Sequences.xlsx`, fetched from canonical location on SharePoint); stored in `plans/<SCHOOL>/`. These files are currently manually managed; we might change that some time soon. Note: we have a template spreadsheet that we can share with you for this purpose.
  - degree rules for each specialisation of interest (e.g. `CEICAH3707.json`); stored in `rules/`; where rules have changed over time, they can be `<stream><program>-<YYYY>-<YYYY>.json` like `CEICDH3707-2020-2025.json` to indicate the start and stop handbook years. The degree rules are stored in a separate repository for ease of management.
  - offerings list in `plans/offerings.json`; this can be copied from the output of `extract-plans` with some manual checking that the courses are indeed in the intended teaching periods. Use `add-offerings` to maintain and normalise this file.

See [FILE-FORMATS.md](FILE-FORMATS.md) for examples of the JSON files used in `rules/` and `plans/`, including common formatting mistakes to avoid.

Example setup:

Obtain the source

```bash
git clone https://github.com/llimeht/transition-checker/
cd transition-checker
git clone https://github.com/llimeht/transition-checker-rules/ rules
```

Install the source into a Python virtual environment (will download the dependencies and create scripts)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

The middle command of `. .venv/bin/activate` to activate the venv temporarily adds the venv with the installed tools to your execution environment; it needs to be repeated each time you start a new terminal.

All the examples below assume that the package has been installed and you have activated the virtual environment; the entry point scripts are used.

Once you have installed the package and obtained the spreadsheet template, you will end up with the following layout to work with (skipping the files in this Python package!):

```text
transition-checker/
├── plans/
│   ├── catalogue.json                             ← extracted from the Handbook Course Catalogue sheet in your spreadsheet
│   ├── course_catalogue_ergs.json                 ← structured prereqs from STU055 ERG report; see "import-erg"
│   ├── offerings.json                             ← known offerings of courses; see "add-offerings"
│   └── CEIC/                                      ← school or specialisation folder you are working in
│       ├── CEIC_Sequences.xlsx                    ← your spreadsheet
│       ├── CEIC_Sequences_offerings.csv           ← teaching periods when spreadsheet assumes courses are running
│       ├── CEIC_Sequences_offerings.json          ← same, but in json format
│       ├── CEIC_Sequences_validation_results.json ← validator output
│       ├── ...
│       ├── catalogue_overrides.json               ← extracted from the Local Course Overrides sheet in your spreadsheet
│       ├── CEICAH3707_2024_T1.json                ← extracted from 'CEICAH3707' sheet in your spreadsheet, 2024 T1 cohort
│       ├── CEICAH3707_2024_T2.degree_rules_overrides.json  ← see "degree-rules --add-override" if needed
│       └── ...
├── rules/                                         ← from the transition-checker-rules repository
│   ├── CEICAH3707.json                            ← rules for a particular program and specialisation
│   └── ...
└── templates/                                     ← map-maker config
    ├── map_steering.json                          ← see map_steering_example.json for details
    └── template_configs.json                      ← extracted from your spreadsheet by extract-templates
```

Notes:

- Manually make your own `plans` and `templates` folders
- The `rules` folder was created by the `git clone` command above. If your rules don't yet exist, you'll need to create them by hand-editing the json files. It's not hard but please feel free to ask for advice.
- `CEIC` is an example school here - replace with whatever is appropriate for your school or specialisation; you can have multiple folders for different schools or specialisations.
- You will need an enrolment sequence spreadsheet for your school or specialisation. The spreadsheet is not included in this repository; please contact the transition project team to obtain a template.
- The `plans/offerings.json` file is a canonical list of intended teaching periods for courses; it is not automatically generated from the spreadsheet. Use `add-offerings` to maintain this file; if you are convinced that the terms you have in your spreadsheet are correct, you can copy the `plans/CEIC/CEIC_Sequences_offerings.json` file to `plans/offerings.json`. Do check it manually. Use `add-offerings --validate` to canonicalise and sort it.

## Checking enrolment plans

### Perform validation of all plans in a spreadsheet

This will extract all plans from the spreadsheet and validate them against degree rules, prereq rules, and intended teaching period offerings.

```bash
plan-validate plans/CEIC/CEIC_Sequences.xlsx
```

To validate only a subset of exported plans, pass a glob that matches the plan filename stem:

```bash
plan-validate plans/CEIC/CEIC_Sequences.xlsx --filter 'CEICKS8338*'
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

```text
Plan has 1 prerequisite/corequisite violation(s):
  [prereq:TEST4000>96uoc] TEST4000 (2027 Term 1): missing 96uoc (has 84uoc)
```

And you can then make a permanent override of this error with `--add-override` if you've made
the academic decision that this is OK for the students, justified for this transition,
and will be handled via advice/individual approvals, rather than a rule change in the handbook:

```bash
degree-rules \
    rules/TESTAH1234-2020-2025.json \
    --plan plans/TEST/TESTAH1234_2025_T2.json \
    --add-override 'prereq:TEST4000>96uoc'
```

```text
Plan status: ACCEPTED
```

The overrides are stored next to the plans (with `degree_rules_overrides` included in the filename)
and can be edited/deleted by hand.
The above override was created in `plans/TEST/TESTAH1234_2025_T2.degree_rules_overrides.json`

## Reporting and governance

### Build consolidated HTML reports from validation results

Combine multiple `*_validation_results.json` files into one sortable/filterable HTML report:

```bash
report-generator report \
  plans/CEIC/*_validation_results.json \
  --filter '*3707*' \
  --output plans/CEIC/validation_consolidated_3707.html
```

The generated table includes:

- JSON filename
- plan / cohort
- intake year / term
- computed exit year / term
- computed duration (years, 1 decimal)
- validation findings summary
- validation status (`OK`, `FAIL`, `ACCEPTED`)
- graduation outcome / adjustment type
- reviewer notes / student notes
- impact assessment status

Notes:

- Rows with `skipped_placeholder` status are excluded.
- The `report` subcommand expects one or more validation result files (explicit paths or shell-expanded globs).
- The HTML report uses a CDN-hosted `simple-datatables` bundle for in-browser sorting/searching.

### Pack validation results into a single file

`report-generator pack` is reserved for the next implementation phase and currently returns a non-zero status with a placeholder message.

## Making enrolment plans

With degree rules, prereq information, offerings, and available teaching periods,
the tool actually has enough information to try to make progression plans,
including across the calendar transition.

It's worth noting that generating enrolment plans
(a) is not deterministic because there are many different options,
(b) can be an overconstrained problem such that there is not actually a possible solution, and
(c) might have a solution but the algorithm might not find it within the allowed time/iterations you've permitted it.

It is therefore worth asking the tool to try generating a few different enrolment plans and then academic judgement
can be used to select the best one. If you get back fewer solutions than you asked for, it's because the solver
ended up with the exact same solution more than once.

The `map-maker` needs to extract some data from the mapping spreadsheet as a starting point:

```bash
extract-template \
  --catalogue-output plans/catalogue.json \
  --template-output templates/template_configs.json \
  plans/plans/CEIC/CEIC_Sequences.xlsx
```

### Generate one or more plan options

By default the plans are printed to the terminal in CSV format that you can paste into a spreadsheet to inspect and
tweak if necessary.

```bash
map-maker \
  --rule rules/CEICDH3707-2026-2029.json \
  --intake "2026 T1" \
  --num-solutions 4 \
  --restarts 4 \
  --iterations 200 \
  --show-nonstandard-periods
```

Copy whichever version of this plan you like back into the planning spreadsheet.

Add `--output some-sequences.csv` to the above command to get the output placed into a CSV file.

Add `-v` (for verbose output) to include some technical information about the relative scoring of the plans that have been
made, and highlight any potential validation errors already detected in trying to find a solution.

The `--show-nonstandard-periods` option includes summer and winter terms so that the rows should exactly match the mapping spreadsheet format to let you paste in the course codes more easily.

### Use a partial plan as a basis for a full plan

There are many cases where it is appropriate to pre-populate part of the enrolment plan with a sequence of courses,
for example, using already published enrolment sequences that get to the end of 2027.

`map-maker` can be fed this partial plan - start by populating this published enrolment sequence into the the Excel file with
the enrolment plans. Note that any teaching period with *any* courses in it will be left untouched in trying to complete
the enrolment plan.

As well as the template data (see `extract-template` above), the tool now needs the partial plans.
Export all the plans (including the partial plan) using either the same `plan-validate` commands from earlier,
or directly with `extract-plans`:

```bash
extract-plans \
  --output-dir plans/CEIC/ \
  plans/CEIC/CEIC_Sequences.xlsx
```

Complete the partial plan:

```bash
map-maker \
  --rule rules/CEICDH3707-2020-2025.json \
  --intake "2025 T3" \
  --target-end "2029 S1" \
  --partial-plan plans/CEIC/CEICAH3707_2025_T3.json \
  --show-nonstandard-periods \
  -v
```

Once again, copy whichever version of this plan you like back into the spreadsheet.

`--target-end` is an optional indication of when the plan should try to ensure that a student
has completed the plan.
Accepts a full intake-style boundary (e.g., `"2027 Term 3"` or `"2028 S1"`).
The planner applies the steering weight `post_target_period_penalty` to each course scheduled
*after* that exact slot; setting this option will cause the planner to use Summer/Winter terms
rather than allowing the enrolment to spill into additional regular teaching periods.
This is just another penalty term against possible solutions and like all the other weightings
included in the solver, this is not a hard constraint and can be violated if there is no choice.

### Steering hints to tune `map-maker` behaviour

The optional steering file can influence plan shape without changing the rule set.

The default steering file is `templates/map_steering.json` but alternative files can be specified
via the `--steering templates/masters_programs_steering.json` option.

The steering file allows tweaking of all weightings that are used in the optimisation. Hopefully,
the defaults are fine for most users.

Typical uses:

- prefer a year or period for a course
- prefer one branch of an `or` clause
- encourage one course to appear before another (as a 'soft' prereq)

An example steering file is provided (`map_steering_example.json`). As `json` format does not support comments, all they keys starting with `_` are comments to document the file; they can be left there as they are ignored when it is read in.

### Search Tuning Notes

The most important planner controls are:

- `--restarts`: number of independent baseline attempts; restarts will fill the baseline plan in different ways based on some jitter parameters.
- `--iterations`: number of permitted course optimisation moves per restart
- `--patience`: early-stop threshold when a restart stops improving (defaults to 25% of the iterations)
- `--ruin-fraction`: how large ruin-and-recreate moves are

Practical guidance:

- increase `--restarts` when you want more options to choose from - there are 4 different algorithms for getting a starting position prior to the refining step, and many academic rules are so highly constrained that more than 4 restarts will not produce any additional sequences
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

### Planning spreadsheets

The planning spreadsheets are the primary source of truth for the transition plans. They are maintained by the transition project team and contain the following sheets:

- Instructions and Glossary - this has details about how to use the spreadsheet and what the various columns mean.
- TEMPLATE - duplicate this sheet for each program/specialisation to be planned.
- Handbook Course Catalogue - this contains the official course catalogue information for all courses in the program/specialisation, including the prerequisite text from the STU054 report.
- Local Course Overrides - updates/overrides to real courses and details of placeholder courses.

Each program/specialisation that needs to be planned has its own sheet. The academic rules file that will be used at validation time is derived from the sheet name - see below.

Each program/specialisation sheet contains a header block and the intended enrolment sequences for each intake.
The header block consists of:

- program/specialisation code - this can be used for whatever is meaningful for you, but is typically as described below
- career (undergraduate or postgraduate) - this is used for course lookup formulae
- UoC - this is used in formulae for quick checks of the total UoC in your plan

Each intake cohort is represented by a further single row header containing the year and teaching period of the intake (e.g. `2026 T3`).
The plan is then represented by rows containing the course codes for each teaching period, with each column representing a teaching period (e.g., T1, T2, S1, S2, Summer, Winter).
The template contains the typical number of rows needed for courses in each teaching period, but you can add more rows if needed.
The template contains non-standard teaching periods (Summer and Winter) to allow for the possibility of courses being offered in these periods, even though they will not typically be used.

**Plan code** is intended to mean what you need it to mean for your work. It will typically be the same as the plan code that appears on a transcript (e.g. `CEICAH3707`). If you have a double major or a double degree, you can concatenate the plan codes (e.g. `MATSM13132+CEICAH3132`).
The plan code can be extended with a description that is useful for your work; this can be configured to appear as a plan description in the visualised output of the plan; the plan description is given in parentheses.
Examples:

- `CEICAH3707` -> `plan_code=CEICAH3707`, `plan_description=""`
- `CEICKS8338 (48 UoC RPL)` -> `plan_code=CEICKS8338`, `plan_description="48 UoC RPL"`
- `CEICKS8338 (suggested enrolment plan)` -> `plan_code=CEICKS8338`, `plan_description="suggested enrolment plan"`

**Sheet name** will often be the same as the plan code, but you will want something shorter if your plan description is a few words.
The name needs to be unique within your workspace and should not contain any special characters (e.g. `*`, `?`, `:`) that are not valid in a filename or an Excel worksheet name.

In the simplest scenario, the `CEICAH3707` sheet will be validated against the `rules/CEICAH3707.json` file.
However, parenthesised description text will also be stripped in an effort to locate the matching rules file:

1. **Exact match** — `rules/<plan_code>.json` or `rules/<plan_code>-YYYY-YYYY.json` (a year-ranged variant).
2. **Trailing `(...)` stripped** — e.g. `CEICKS8338(48RPL)` → try `CEICKS8338`.
3. **Everything after the rightmost `_` stripped** — repeated until a match is found (e.g. `CEICAH3707_Accelerated_Plan` → `CEICAH3707_Accelerated` → `CEICAH3707`).
4. **Everything after the rightmost `-` stripped** — repeated until a match is found.

If no match is found after all crops are exhausted, a warning is logged and `program_metadata` will be `null` in the exported plan.

Once the effective base code is resolved, **year-range selection** is applied separately: if multiple rule file versions exist for a code (e.g. `CEICDH3707-2020-2025.json` and `CEICDH3707-2026-2029.json`), the file whose year range covers the intake year is used. If the intake year falls outside all ranges, the plain `<code>.json` file is used instead.

### Manage the offerings list

Canonicalise and sort an offerings file in place:

```bash
add-offerings plans/offerings.json --validate
```

Add one or more teaching periods for a course (creates the entry if absent):

```bash
add-offerings plans/offerings.json --schedule CEIC2001 T1 T3
```

Add one or more teaching periods for one explicit calendar year:

```bash
add-offerings plans/offerings.json --year 2026 --schedule CEIC2001 T1
```

Periods are accepted in any alias form (`T1`, `term 1`, `S2`, `semester 2`, `summer`, etc.) and stored in canonical display form. Unknown period names cause a non-zero exit and leave the file unchanged.

An list of intended teaching periods can be exported, with an optional filter pattern
for which courses to include, and output either as plan text on the terminal or as CSV.

```bash
add-offerings plans/offerings.json --show '*'
add-offerings plans/offerings.json --show 'CEIC*'
add-offerings plans/offerings.json --show 'CEIC*' --output offerings.csv
add-offerings plans/offerings.json --show 'CEIC*' --show-by-year
```

### Prerequisite information

Prerequisite information is obtained from 3 sources and layered in the following order:

- the planning spreadsheet contains data from the STU054 report. The JSON plan files and the `plans/catalogue.json` file that are is extracted from it therefore also contain these data. This is the lowest priority source of prerequisite information.
- the `plans/course_catalogue_ergs.json` file contains structured prerequisite information extracted from the STU055 ERG report. This is the middle priority source of prerequisite information (and in practice should override the prereq information for almost every course). This file must be manually obtained (see below).
- the `plans/catalogue_overrides.json` file contains overrides that are manually added to the catalogue via the `add-overrides` command; it is recommended that you do not use this source.
- the "Local Course Overrides" sheet in the spreadsheet (as extracted into the `plans/CEIC/catalogue_overrides.json` file) contains overrides that are manually added to the catalogue; this is the highest priority source of prerequisite information.

The `plans/course_catalogue_ergs.json` file contains coded data that reflects the actual implementation of the prerequisite rules in SiMS for *almost* all prerequisite relationships; the other files contain human-generated text that needs to be interpreted by the parser in these tools. If you are overriding prerequisite information that is a simple set of course codes, then write them with `(`, `)`, `AND`, `OR` and it will likely parse correctly.

#### Import structured prerequisite data from the ERG report (STU055)

The original tracking spreadsheet contains a column of prerequisite text copied
from the STU054 report. This text is often ambiguous, malformed, or otherwise
impossible to parse correctly and is not the authoritative prereq information in any case.
The STU055 ERG report contains a machine-readable version of most of the prerequisite
information that is much more reliable.

It is recommended that you obtain the STU055 data and feed that to these tools.
This can be done from the report yourself in spreadsheet format or by obtaining the
parsed data in `json` format.

The `import-erg` command reads a spreadsheet export from the **STU055** report
**Attached ERG Details**. The `ERG Requisite Detail` rows are ingested into a
structured expression tree and stored in `plans/course_catalogue_ergs.json`.

```bash
import-erg \
  "STU055 Attached ERG Details.xlsx" \
  --output plans/course_catalogue_ergs.json
```

Additional options:

- `--export-excel updated_ergs.xlsx`: this output can be copied into the Handbook sheet in the sequencing spreadsheet to improve that data; it contains the merged prerequisite text from the STU055 report and the handbook course title/UoC data from STU054.
- `--fallback-report fallback_report.json`: this output is for developers in identifying patterns that the parser does not yet handle.  Use `python3 tools/erg_analyse_fallbacks.py fallback_report.json` to get a frequency table of unresolvable line shapes.
- `-v` or `--verbose`: print out the ERG expression tree for each course as it is processed.

Further details of the ERG expression tree are documented in the the `FILE-FORMATS.md` file.

**Caveat on the STU055 data:** the STU055 ERG report contains some `RQ` rule pointers that point to data that is not available in the data export. The data only contains a human-written description of that rule which is (a) often incomplete, (b) needs to be interpreted, and (c) often does not contain the actual details in an interpretable form. The `import-erg` command will report any of these missing rules and the `--fallback-report` option can be used to help identify patterns that are not yet handled by the parser. You may need to override the prerequisite information for some courses that are affected by these missing rules.

#### Override a prerequisite information in the catalogue

Some handbook prerequisite information may need to be updated from what is currently approved based on changes that you know will be made.
However, it is recommended that you instead do this via the Local Course Overrides sheet in the spreadsheet and then re-extract the catalogue, rather than manually editing the `catalogue.json` file.
That way the spreadsheet acts as a single source of truth for the overrides, and implementation of your intended changes can be tracked.

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

## Legacy Tools

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

## To-do

## Licence and credits

This work was developed by Stuart Prescott from the School of Chemical Engineering
as part of the UNSW 3+ to Flex-semester transition project.

Copyright 2026 UNSW Sydney

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS “AS IS” AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
