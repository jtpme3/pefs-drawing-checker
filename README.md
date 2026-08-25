# PEFS drawing checker

Automated pre-issue checking of Origin gathering PEFS drawing PDFs, against
`ARN10-G-LIST-0135 Rev A - PEFs Drawing Checklist`.

Drop the final PDFs in a folder, point the tool at it, open the Excel report.

```
python check_drawings.py "C:\path\to\final drawings" -o "report.xlsx" -m
```

Options:

| | |
|---|---|
| `-o FILE` | name the report (default `PEFS check report <date time>.xlsx`) |
| `-m [DIR]` | also write marked-up PDFs (default folder `marked-up`) |
| `-r` | search sub-folders too |

Exit code is 2 if anything FAILed, so it can be wired into a batch script.

## Marked-up PDFs

With `-m`, each drawing is copied out with its findings on it:

- a **numbered callout box** at the tag the finding is about — the offending
  valve, the line number with the unassigned sequence, the mistyped wellsite —
  colour coded red for FAIL and amber for REVIEW;
- the **full finding text in each box's comment (popup) metadata** — hover or
  click the box in any PDF reader to read it.

No text is drawn onto the sheet and no comment page is appended (Jordan,
2026-08-20) — the boxes and their comments are the whole deliverable.

The drawing itself is never modified. The markup is annotations layered over
it, so the page content stream stays byte-identical and any PDF reader can turn
the comments off. Findings are located by the tags they quote; a finding
that names no tag (file metadata, a batch comparison) is flagged with a
numbered box in the sheet margin so nothing is silently dropped.

## Why PDF and not DWG

The PDFs are the better target, despite being the "flattened" artefact:

- **They are already machine-readable.** AutoCAD plots these drawings with SHX
  fonts, so the visible text is stroked geometry and normal PDF text
  extraction returns nothing. But the plot config has *capture SHX fonts as
  comments* switched on, which writes every SHX string into the PDF as an
  annotation with its position. That gives a complete, positioned text layer
  with **no OCR** — exact strings, no recognition errors.
- **You check what the client actually receives.** Wrong sheet plotted, stale
  PDF, wrong filename, revision that does not match the frame — none of that
  is visible in the DWG, because the DWG can be perfect while the issued PDF
  is wrong.
- **No licence or install needed.** Runs headless over hundreds of files. A
  DWG route needs AutoCAD or the ODA File Converter on the machine.

A DWG/DXF back-end would beat PDF for geometry-heavy checks later — layers,
block attributes, and true line weights are all explicit there, where the PDF
only has stroked paths. Worth adding as a second front-end when the checks
move into that territory, but not needed for anything in this slice.

**Important caveat:** the whole approach depends on that SHX capture option
being on when the PDF is plotted. If it is off, the tool recovers no text — so
it reports `EXT-01 FAIL` loudly ("re-plot with that option on") rather than
passing everything by default. Full-page raster scans are flagged the same
way.

## What it checks

Every check is decidable from the text, so a FAIL is always a real defect.
Anything needing judgement is raised as REVIEW instead.

**Drawing frame and identity**

| Code | Check | Checklist row |
|------|-------|---------------|
| EXT-01 | PDF carries a readable text layer | tool prerequisite |
| TB-00 | Origin drawing frame recognised | Title Block |
| FN-01 | Filename follows a known convention (Promech or Origin) | 81 |
| FN-02 | Filename drawing number matches the title block | 14, 81 |
| FN-03 | Filename revision matches the title block | 14, 81 |
| FN-04 | Filename includes the drawing title (Promech naming) | 81 |
| FN-05 | Filename wellsites agree with the title block | 15, 40, 81 |
| TB-01 | Drawing number present and well formed | 11, 38 |
| TB-02 | Revision present and valid (`A`, `0`, `0A`, `P01`) | 13, 42 |
| TB-03 | Revision matches the latest revision history row | 46 |
| TB-04 | Latest revision row has a date and a description | 46 |
| TB-05 | Drawing title populated | 15, 39 |
| TB-06 | Title identifies the drawing as a PEFS | 39 |
| TB-07 | Title names every wellsite drawn | 15, 40 |
| TB-08 | Project number box left empty (Origin gathering convention) | 17, 43 |
| TB-09 | Project number agrees with the title | 17, 43 |
| TB-10 | CADFILE printed under the drawing border | 16, 41 |
| TB-11 | CADFILE names this drawing (handles `188_194.DWG` ranges) | 16, 41 |
| TB-12 | Sign-off boxes correct for the revision type | 18, 44 |
| TB-13 | Reference drawings table populated | 19, 47 |
| TB-14 | Reference drawing entries well formed | 19, 47 |
| TB-15 | "Drawing derived from" note present on P revisions | 22, 48 |
| TB-16 | Frame dates use one consistent format | 18, 46 |
| TB-17 | PDF layout name is consistent with the drawing | 14 |
| TB-18 | Title block signatures match the Rev 0 issue | 18, 44 |
| TB-19 | "Derived from" note uses the standard wording | 22, 48 |

**Drawing body, notes and metadata**

| Code | Check | Checklist row |
|------|-------|---------------|
| SP-01 | Spell check of all drawing text (batch-consensus) | 35, 49 |
| LN-01 | Line numbers present and fully assigned (no `XXXX` placeholders) | 24, 54 |
| LN-02 | Line number project codes match the drawing | 24, 54 |
| TAG-01 | Crossing, tie-in and drain tags well formed | 26, 29, 61 |
| TAG-02 | Tags reference a wellsite drawn on the sheet | 26, 29, 61 |
| TAG-04 | Crossing tag prefix matches the written crossing type | 26, 61 |
| SIZE-01 | No OD560 piping (recommend sizing up to OD630) | 24, 54 |
| FLOW-01 | Continuation flags show flow direction | 20, 21, 28, 67 |
| FLOW-03 | Flow leaves the sheet (wellsites gather toward the trunkline) | 28, 67, 68 |
| PLT-01 | PDF plotted no earlier than its revision date | 82 |
| PLT-02 | PDF came straight from the AutoCAD plotter | 82 |

**Pipe specification and valves**

| Code | Check | Checklist row |
|------|-------|---------------|
| SPEC-01 | Line service matches its pipe specification | 24, 54 |
| SPEC-02 | Line size within the specification's range | 24, 54 |
| VLV-01 | Valve type suits the service it is on | 62, 63 |
| VLV-02 | Valve size within its permitted range | 62, 63 |
| VLV-03 | Valve permitted by the specification of its line | 62, 63 |

**Geometry**

| Code | Check | Checklist row |
|------|-------|---------------|
| GEO-01 | Changes clouded for this revision | 33, 58 |
| GEO-02 | Line weights differentiate gas from water | 32, 70 |

**Notes** (see *Note checking* below)

| Code | Check | Checklist row |
|------|-------|---------------|
| NOTE-01 | Note references resolve; no orphan notes | 76, 78 |
| NOTE-03 | Note wording consistent across the set | 76, 78 |

**From the client's checkprint** (see *Client comments* below)

| Code | Check | Checklist row |
|------|-------|---------------|
| CMT-01 | Development package name matches its official name | 15, 39 |
| CMT-02 | Reducer labels read larger x smaller | 63 |
| CMT-03 | Valve tags spelled correctly | 62, 63 |
| LN-03 | Line sequence numbers not duplicated | 24, 54 |

**Against the MAOP review calculation**

| Code | Check | Checklist row |
|------|-------|---------------|
| MAOP-01 | Line numbers agree with the MAOP review calculation | 23, 24, 53, 54 |

**From the DXF only** (see *What the DXF adds* below)

| Code | Check | Checklist row |
|------|-------|---------------|
| DXF-01 | Drawing symbols recognised (coverage report) | tool coverage |
| DXF-02 | Flow direction shown on the pipework | 28, 67, 68 |
| DXF-03 | Property boundaries drawn dashed | 25, 60 |
| DXF-04 | Gas and water pipework on their own layers | 32, 70 |
| DXF-05 | Continuation flags carry a line number | 20, 21, 52 |
| DXF-06 | Pipework joins up *(informational only)* | 21, 28 |
| CONT-01 | Continuation references resolve and point back | 20, 21, 52 |

**Across the batch**

| Code | Check | Checklist row |
|------|-------|---------------|
| WELL-01 | Wellsite tags spelled consistently | 26, 30 |
| CONT-02 | Continuations naming an in-set drawing use its issued number (typo or missing P modifier = FAIL; other-package targets are not findings) | 20, 21 |
| TAG-03 | HPV / LPD / crossing identifiers unique within the batch | 26, 29, 61 |
| REF-01 | Reference drawings use the in-set drawing numbers (P modifiers) | 19, 47 |
| FLOW-02 | Continuation flow directions agree between sheets | 20, 21, 28, 67 |
| TIP-01 | Tie-in point numbers unique within the batch | 26, 29, 61 |
| BAT-01 | Drawing number unique within the batch | 11, 81 |
| BAT-02 | Sheet consistent with the rest of the batch | 81 |

### Pipe specifications

`drawing_checker/specs.py` holds seven specs. Five are transcribed by hand from
the spec sheets in `reference/`; two are known only from the MAOP review
calculation and a client comment, and are marked as such.

| Spec | Material | Service | Pipe OD | Valves | Source |
|------|----------|---------|---------|--------|--------|
| P126 | PE100 SDR 13.6 | water | 20–1200 | VB17, VB17E (63–400), VF18 (50–900) | spec sheet Rev 10 |
| P128 | PE100 SDR 11 | water | 20–1200 | VB17, VB17E (63–400), VF18 (50–900) | spec sheet Rev 10 |
| P150 | PE100 SDR 7.4 | water | 20–450 | VB32 (OD125) | spec sheet Rev 2 |
| P151 | PE100 SDR 21 | gas | 32–1200 | VB16, VB16E (63–630), VF18, VG08 (400–900) | spec sheet Rev 1 |
| P153 | PE100 SDR 17 | gas | 25–1200 | VB16, VB16E (63–630), VF18, VG08 (400–900) | spec sheet Rev 1 |
| P130 | PE100 SDR 9 | water | unknown | VB32 | MAOP calc + client comment |
| P157 | PE100 SDR 11 | gas | unknown | none listed | MAOP calc |

P130 and P157 have no spec sheet, so their size ranges are left open rather
than guessed, and a valve on a P157 line reports NO DATA rather than a FAIL.
Any other spec code reports NO DATA — never a pass.

They are transcribed rather than read at check time because three of the five
sheets are scans with no text layer at all, and the one that carries an OCR
layer garbles it badly enough to be unusable ("CATALOGUE MOULDED FITTINGS"
comes out as "CAIALUGUE MOULUEDFILLINGS"). Feeding that into a FAIL would be
worse than not checking. Each entry records the source file and its revision,
and `spec_source_warnings()` warns if the folder ever holds a different
revision from the one transcribed.

A line's spec is only checked if it is one of these five. Lines on P130 and
other specs not supplied report NO DATA — never a pass.

Valve tags are `<service><size><type><code>`, e.g. `RG125VB16`. Because the
tag carries both the service and the size, `VLV-01` and `VLV-02` need no
geometry at all and are fully objective. `VLV-03` has to associate a valve with
a line, which is a spatial guess — so where more than one candidate line spec
is plausible, it reports REVIEW rather than FAIL.

On some sheets (the DP094 set) the SHX capture splits a valve tag into two
annotations — `RG` in one, `630VB16` in the other. The checker reunites a
prefix-less tag with a service item butted against its leading edge before
checking, so those valves are not reported as carrying no service.

### Spell checking

A plain dictionary check is useless on a PEFS — roughly a quarter of the words
are tags, initials and Origin vocabulary, so it flags ~77 "errors" of which
none are real. `SP-01` stays quiet on real drawings by combining three things:
tags, line numbers and file paths are never treated as words, it carries a
curated domain dictionary, and it uses **batch consensus** — a word used
across several drawings is house vocabulary, while a word appearing on one
drawing only that is a near-miss of a common word is a typo. On seeded errors
it catches `SYMOLOGY`→`SYMBOLOGY`, `ENGINEERNG`, `FACILITES` and the
transposition `POITN`→`POINT`.

Since 2026-08-20 it reads **all text on the drawing** (Jordan's request), not
just the title-block prose. The accepted cost is the odd property-owner or
locality name raised for review (`DOUGALL`, `MEROO`) — a dictionary cannot
know rural Queensland; everything else stays at zero false positives.

`WELL-01` covers the same class of error for *tags*, which the speller
deliberately ignores. It caught `PSH059`/`PSH079` for `PHS059`/`PHS079` on a
sheet that also spells them correctly, while correctly leaving `CNH001` alone —
that is a real wellsite, two edits from `CMN`, not a typo.

### Statuses

| | |
|---|---|
| **PASS** | Checked and correct |
| **FAIL** | Objectively wrong — fix before issue |
| **REVIEW** | Needs an engineer's eye (the checklist's "within reason" items) |
| **NO DATA** | The field could not be read — never treated as a pass |
| **N/A** | The check does not apply to this sheet |
| **SKIPPED** | Not an Origin drawing sheet; not checked |

## The report

- **Summary** — one row per drawing, worst first, with a count of each status
  and the top issue. Non-drawings are listed separately at the bottom.
- **Findings** — one row per thing to look at, with what was actually found.
- **Matrix** — checks down, drawings across: the same shape as the manual
  checklist.

## Validating the tool

```
python tests/test_checks.py
```

Checks field extraction against Q-4300-10-DF-193 Rev 1 (verified by hand), then
generates mutants of it — annotations stripped, filename revision changed,
drawing number changed, filename convention broken — and asserts the right
check fires for each. Most importantly it proves that a drawing the tool
*cannot* read fails loudly instead of passing.

## The MAOP review cross-check

`MAOP-01` reads `ARN10-P-CALC-0029` from `reference/` and, for every corridor
drawn on a sheet that the calculation covers, checks the drawing carries the
line number the calculation sets.

Two parts of the line number are deliberately ignored: the **sequence** (the
calculation writes `XXXX` throughout) and the **trailing project code**, which
is changed on the drawings to match the area number. What is compared is size,
service and pipe spec — `900-RG-…-P151` against `900-RG-…-P151`.

Corridors not in the calculation report N/A, so a run over drawings from
another development package stays quiet rather than crying wolf.

## Note checking

- **NOTE-01** — every `NOTE n` callout resolves to a note that exists, and no
  gaps in the numbering. Notes that are never called up are raised for review,
  except `DELETED` placeholders (which hold numbering stable) and blanket
  notes that open "ALL…", "UNLESS…", "REFER…".
- **NOTE-03** — the same note worded two ways across a drawing set. The
  majority wording is taken as the set's intent and the odd ones out are
  flagged, quoting both wordings from the point where they diverge. Notes are
  matched as "the same note" on word overlap and a shared opening subject (the
  standard and older LPD collection notes share only 54% of their words yet
  both open "LPD COLLECTION").

Notes are deliberately **not** compared against a standard notes list. That
was tried (NOTE-02) and flagged 11 of 14 real sheets over legitimate wording —
noise. Internal consistency across the set is the check.

## Continuation flow direction

A continuation flag is a pennant: a long box holding the target drawing
number with a chevron point at one end, and **the chevron points in the
direction of flow**. An apex facing the sheet border is flow *out* onto the
target drawing; an apex facing into the drawing body is flow *in* from it.
Trunk/corridor lines are drawn with a pennant pointed at *both* ends, and a
both-ended pennant always meets a both-ended pennant on the neighbouring
sheet. The convention was measured, not assumed — it holds across every flag
in the ARN12 DP401, ARN10 DP442 and DP094 sets.

The pennant outline is a single five-or-six-sided shape at a position the
text layer already gives, so reading it is nothing like the rejected
"flow arrow on every bend and tee" geometry check — no network
reconstruction, no false-positive surface.

- **FLOW-01** — every pennant shows a direction (a point at one end, or a
  deliberate both-ended trunk pennant).
- **FLOW-02** — the two sheets of a boundary agree. Flags are matched
  corridor-by-corridor using the line number drawn along the pennant
  (`250-RG-2280` names the same corridor on both sheets), falling back to
  the RAW GAS / RAW WATER service label. A head-on contradiction — both
  sheets showing the line flowing *out* toward each other, or both showing
  it arriving *in* from the other — is a FAIL; a missing counterpart flag or
  a single-ended pennant meeting a both-ended one is REVIEW.
- **FLOW-03** — gathering flow is unidirectional, wellsites toward
  trunklines: a service whose every readable flag points *into* a sheet has
  nowhere to go, which is REVIEW (it is only legitimate when the flow
  terminates at a facility drawn on the sheet).

On the reference sets this found a real defect: DF-094 and DF-095P01 both
draw the pennants for 250-RG-2280 and 125-RW-2416 pointing into their own
sheet, so each claims the flow arrives from the other.

## Tie-in point numbers

**TIP-01** collects every `TIP-<wellsite>-<n>` tag across the batch and
flags a number used on more than one drawing, or drawn twice in different
places on one drawing. It is REVIEW rather than FAIL because the text alone
cannot tell a doubled-up number from the same physical tie-in legitimately
drawn on two adjacent sheets. Measured across the ARN05, ARN10 and ARN12
sets it fires on one tag in 119, so it stays quiet on a clean batch.

## Client comments

`drawing_checker/client_checks.py` implements the comments from
`reference/ARN10 Project Documents/DP442 IFR Checkprint ECT Check.pdf` that can
be decided from the drawing text. `DP_NAMES` there maps a development package
to its official name — add a row whenever one is confirmed.

The comments that need topology (move the reducer upstream of the tee, single
spec break, spec break at the blind, remove the hot-tap vent valve) are listed
at the bottom of that module and stay manual, for the same reason the geometry
checks below do.

## OCR'd drawings are read, but never trusted

If a PDF has no SHX capture but does carry an OCR text layer, the tool reads it
— word tokens are merged back into strings so the label-driven checks work —
and then **caps every finding at REVIEW**. `EXT-02` flags the sheet, and each
downgraded finding says why. Nothing read by OCR is allowed to assert a defect.

That is not caution for its own sake. Measured on the OCR'd IF439 set:

- ~3% of line numbers and ~8% of corridor tags came back malformed —
  `RG` read as `R0`, `R6` or `2G`; `0` read as `O`; `RG125VB16` as `RG12SV816`.
- The set was OCR'd twice in the same file, so the same drawing appears twice.
  **The two passes disagree**: `900-RG-XXXX-P151-4255` vs `…-P151-4259`,
  `COM067-3-2` vs `COM061-3-2`, `250-RG-0854…` vs `250-R0-0854…`. Roughly
  10–20% of tags differ between passes of the same image.

Non-deterministic errors cannot be corrected for, and every precise check here
is a character-level comparison. An OCR run is therefore useful only for
spotting things worth eyeballing — it is not a check. Re-plot with
`PDFSHX = 1`.

## Geometry: what was measured and rejected

Three geometry checks from the checklist were prototyped and abandoned, for
reasons worth recording so they are not re-attempted:

- **Flow arrow on every bend and tee** (rows 28, 68). Needs the pipe network
  reconstructed from ~9,000 stroked paths and each arrowhead matched to a
  vertex. Every prototype raised false positives on legitimate straight runs.
  (The *continuation-flag* flow checks above are the tractable slice of this:
  one known shape at a known position, no network reconstruction.)
- **Property boundaries shown dashed** (rows 25, 60). Not possible from these
  PDFs — every path reports its dash array as `[] 0`, because AutoCAD emits
  dashed linetypes as separate short segments rather than as a PDF dash array.
- **Tag clutter / overlapping labels** (rows 30, 72). The SHX capture rect is
  the annotation square, not a tight glyph box, so stacked label lines such as
  `RG125VB16` above `PHS100-1-1` report as 100% overlapping. Measured 35–91
  "overlaps" per sheet on drawings with no actual clutter.

These stay manual. A DWG/DXF front end would make all three tractable, since
layers, linetypes and true text extents are explicit there.

## Not yet covered

Text-based checks that could still be added:

- standard note text compared against the *Standard Notes* sheet of the
  checklist (row 77)
- HOLD cloud text extracted and listed, so open holds are visible per sheet
  (rows 34, 59)

Genuinely not automatable: "PEF visually represents gathering geography",
"drawing content sensibly aligns with continuation drawings".

## Dependencies

`pymupdf`, `openpyxl`, `ezdxf`, `pyspellchecker` — all pure-Python:

```
pip install -r requirements.txt
```

## Client data is not in this repository

The tool is developed against issued Origin drawings, Origin pipe
specifications and internal standards. None of that is ours to publish, so
`.gitignore` keeps these folders out of git and a fresh clone will not have
them:

| Folder | What belongs there |
|---|---|
| `reference/` | issued PEFS sets, the A-1000-50-DC-Pxxx spec sheets, `ARN10-G-LIST-0135` checklist, MAOP review calc |
| `origin standards/` | Origin pipeline and HDPE standards |
| `samples/` | reference reports and marked-up sets from past runs |
| `output/` | generated reports and markups |

Consequences on a fresh clone:

- **The tool still runs** on any folder of PDFs you point it at.
- **`tests/test_checks.py` will not pass** — every layer of it asserts against
  real drawings in `reference/`. Restore that folder from the project drive
  before validating a change. See CLAUDE.md for which files each layer needs.
- **MAOP-01 reports NO DATA** until a MAOP review workbook is put in
  `reference/`.

`drawing_checker/specs.py` is the exception: the pipe spec tables are
transcribed into source because the spec PDFs are unreadable scans (see the
module docstring). Keep that in mind before making this repository public.

## Layout

```
check_drawings.py        CLI (three passes: read -> check -> batch check)
drawing_checker/
  extract.py             PDF -> positioned text layer (SHX annots + real text)
  titleblock.py          Origin A1 frame field extraction, anchored on labels
  checks.py              frame and identity checks
  content_checks.py      body, metadata and cross-batch checks
  specs.py               transcribed pipe specs + line and valve checks
  geometry_checks.py     revision clouding and line weights
  notes.py               note references, standard wording, cross-set drift
  client_checks.py       checks taken from the client's checkprint comments
  maop.py                cross-check against the MAOP review calculation
  spelling.py            batch-consensus spell checker
  report.py              Excel writer
  markup.py              marked-up PDFs with callout boxes (full text in each box's comment)
tests/test_checks.py     validation: known drawing, mutants, and the ARN set
reference/               checklist, pipe specs, MAOP calc, client checkprint,
                         and the drawing sets
```

Checks run in three passes because the spell checker and the wellsite-tag
check both need the whole batch before they can judge any single sheet.

## What the DXF adds

A DXF is read into the same `Sheet` shape as a PDF, so all the frame, body,
spec, note and batch checks run unchanged. What it adds is everything a PDF
flattens away:

- **Text is exact and unconditional** — no SHX capture setting, no OCR, no
  dependence on how someone plotted.
- **The title block comes from block attributes** (`DOCUMENT`, `REF1`, `REF2`)
  rather than from reading positions.
- **Layers and linetypes are explicit**, which is what makes DXF-03 (dashed
  property boundaries) possible at all — from a PDF every path reports its
  dash array as `[] 0`.
- **Symbols are named blocks with attributes**, so a valve is
  `VALVESPEC='RG125VB16'` rather than a string to be pattern-matched, and a
  continuation flag carries `DOCNO` and `LINENO`.

Three checks about the *issued artefact* are N/A on a DXF and stay on the PDF:
filename convention (`FN-*`), the CADFILE stamp (`TB-10/11`), and plot metadata
(`TB-17`, `PLT-*`). Run the tool on both.

### Coordinates

DXF positions are mapped onto the plotted page, so markup lands on the PDF
without further transformation:

```
model --viewport--> paper --plot scale + origin--> mm --> PDF points
```

The viewport's `view_target_point` is the model point at its centre and
`height / view_height` the scale; the layout's `scale_numerator/denominator`
and plot origin give paper→mm. Validated to within 7pt against the plotted
sheet.

### Block naming

Three drafting lineages name the same symbol differently — a flow arrow is
`tag_flowa`, `Flow Arrow` or `PEF_STAGE-4_OVERALL$0$FLOW`. `BLOCK_KINDS` in
`dxf_extract.py` maps them to semantic kinds, matching on a normalised name.
Anything unrecognised is counted and reported by `DXF-01` rather than silently
ignored — that report is how the table gets extended.

### DXF-06 is informational, deliberately

Connectivity reconstruction works well enough to be useful groundwork but not
well enough to call a gap: 16–44% of pipe endpoints come back "loose" on every
sheet in the IF439 set, with no evidence any of them has a real defect. Runs
are broken by symbols whose extent is unknown (only an insertion point), by
hatches and by leaders. It never fails, and it is what the spec-break-position
and reducer-upstream-of-tee checks will be built on once there is a drawing
with a known gap to calibrate against.

## Getting DXFs

`SAVEAS` → *AutoCAD 2018 DXF* in AutoCAD, or the free **ODA File Converter**
for a batch without AutoCAD, or `tools/batch-export/Export-AcadBatch.ps1`
which drives `accoreconsole.exe` headlessly and writes DXF and PDF together.
Ask for **DXF 2018 (or 2013) ASCII**; R12 drops modern entity types. If the
title block is an xref it must be bound, or the frame will not come through.

## Marked-up DXFs

When the input is a DXF, `-m` writes a marked-up **DXF** as well as the
marked-up PDF:

- a **red / amber box** around the item each finding is about, with a numbered
  tag and a one-line label beside it;
- everything on `CHECK-FAIL`, `CHECK-REVIEW` and `CHECK-INFO` layers, so it can
  be frozen while working or purged when the comments are cleared;
- a **`CHECK COMMENTS` layout** carrying the numbered schedule with the full
  text of every finding, ready to plot as its own sheet.

The markup goes in **paper space**, which is why it lands exactly where the
equivalent PDF callout does — page coordinates map straight to paper space. The
source DXF is never modified; a copy is written, and re-running purges the
previous markup rather than stacking a second set on top.

Because callouts are in paper space they do not move if the viewport is panned
or zoomed. On an issued sheet that is what you want; if the drawing is being
actively re-laid-out, re-run the check afterwards.
