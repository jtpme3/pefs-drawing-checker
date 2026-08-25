# ocr-drawing-checker — project notes

Automated pre-issue checking of Origin gathering PEFS drawing PDFs against
`reference/ARN10-G-LIST-0135 Rev A - PEFs Drawing Checklist.xlsx`.
See `README.md` for usage and the check list.

Despite the folder name, **no OCR is involved** — see below.

## Facts established by inspection (don't rediscover these)

- Origin gathering drawings are plotted from AutoCAD with **SHX fonts**, so
  `page.get_text()` returns only the TrueType Origin address block (~20 words).
  The drawing text is stroked vector geometry.
- The plot config has **"capture SHX fonts as comments"** on, so every SHX
  string is also a `Square` annotation whose `/Contents` is the string. That is
  where all the readable text comes from.
- Annotation rects are in **unrotated page space**; these pages carry
  `/Rotate 270`. Multiply by `page.rotation_matrix` to get displayed
  coordinates. `page.get_text("words")` is already in displayed space — do not
  rotate those.
- Sheets are A1 frames plotted to **A3, 1191 x 842 pt**, landscape.
- Frame layout is stable to within ~3pt across DP packages, but extraction is
  still anchored on the printed labels rather than absolute coordinates.
- Multi-line MTEXT captured as SHX **duplicates the first physical line of each
  paragraph** (`"1. FOO BAR FOO BAR BAZ"`). `extract.dedupe_shx_repeat()`
  undoes it. Relevant once note-text checks are added.
- Origin's address logo block is real TrueType text sitting in the same band as
  the reference drawings table, and overlaps it in both x and y. The table
  parser separates them by `TextItem.source` (`"shx"` vs `"text"`).
- ArcGIS-produced maps (`Esri ArcGISPro` in the PDF creator) have real text and
  no Origin frame — correctly skipped as non-drawings.

## Frame geometry cheat sheet (displayed coords, 1191x842 page)

| Field | Anchor label | Value offset from anchor |
|-------|--------------|--------------------------|
| Drawing number | `DRAWING NO.` (~958, 767) | dx -6..22, dy -3..35, h >= 14 |
| Revision | `REVISION` (~1128, 767) | dx -10..22, dy 2..24, h >= 11 |
| Project number | `PROJECT NO.` (~903, 767) | dx -6..40, dy 2..18 |
| Title lines (4) | `TITLE` (~903, 725) | dx -4..22, dy 2..50, h >= 11 |
| Sign-off initials | role label (~822, 734+) | dx 25..42, dy -6..8 |
| Sign-off date | role label | dx 44..64, dy -7..8 |
| CADFILE | — | vertical text at x~22, under the left border |

Revision history and reference drawing tables run along the bottom left
(y 769-793); revision rows are only 5-8 pt apart, so items are assigned to the
**nearest** row, not to any row within a tolerance.

## Design rules

- **Never let an unreadable drawing pass.** `Sheet.has_text_layer` judges
  drawings on SHX count, not raw item count, precisely because the TrueType
  address block survives when the SHX capture is off. If a page looks like a
  drawing (landscape, >= 1000pt wide, heavy vector content or a full-page
  image) but has no readable text, `EXT-01` FAILs.
- **FAIL means objectively wrong.** Anything requiring judgement is REVIEW.
  This is what makes the report trustworthy — an engineer should be able to act
  on a FAIL without opening the drawing.
- **A missing field is NO DATA, not a pass.**
- Non-drawings in the folder are SKIPPED and listed separately, never FAILed.

## Working on this

Validate after any change to extraction or checks:

```
python tests/test_checks.py
```

Three layers of test:

1. Field extraction against `Q-4300-10-DF-193 Rev 1`, verified by hand. That
   file still lives in `claude-files\Projects\ctr-generator\reference\ARN05\
   G - GENERAL\` — if that project moves, copy the PDF into `samples/` and
   repoint `REFERENCE`.
2. Mutants of it (annotations stripped, revision changed, number changed,
   filename convention broken) to prove the checks fire.
3. The real ARN05 + ARN10 set in `reference/`, asserting the specific defects
   found in them and — just as important — that the sheets which are clean stay
   clean. This is the regression guard against a check going noisy.

Verify generated workbooks in real Excel via pywin32 `DispatchEx` from the Bash
tool (PowerShell COM is broken on this machine).

## Origin revision formats

`A`/`B` (review), `P01` (preliminary), `0`/`1`/`2` (issued), and **`0A`** — an
issued revision re-issued for review. `0A` is real and appears throughout the
ARN10 set; the regex in `titleblock.REVISION_VALUE` must keep accepting it, and
getting it wrong also silently merges two revision-history rows into one.

The title block signature block records the **Rev 0** issue (checklist row 18),
not the current revision. On a Rev 0A drawing the boxes correctly show the Rev 0
signatures and dates. `TB-18` reconciles the two.

## Calibration notes (why checks are tuned as they are)

Tuned against the 14 real ARN05/ARN10 drawings in `reference/`:

- **PROJECT NO. blank is normal** on gathering jobs (checklist row 17 says so).
  It was firing REVIEW on 14/14 — now PASS when empty.
- **A wellsite PEFS legitimately shows neighbouring wellsites** for tie-ins and
  continuations, so `TB-07` only flags one drawn about as often as the subject
  (>= 50% of the busiest tag). Was 11/14, now 5/14.
- **Layout tab named "Rev 0" on a rev 0A drawing** just means the tab was not
  renamed; `TB-17` treats a tab revision that prefixes the current one as fine.
  It also accepts a tab naming the *source* revision of a derived P-rev.
- **Plots are made the evening before the nominated issue date**, and PDF
  timestamps use inconsistent time zones, so `PLT-01` allows one day of grace
  (`PLOT_GRACE_DAYS`) before calling a plot stale.
- **`WELL-01` blames only the sheet carrying the rarer spelling.** Flagging
  every sheet that shares the wellsite number blamed innocent drawings.

## Pipe specs are transcribed, not parsed

`specs.py` holds P126/P128/P150/P151/P153 typed out by hand from the sheets in
`reference/`. **Do not replace this with OCR.** P128, P151 and P153 are pure
scans with zero text; P126's existing OCR layer renders "CATALOGUE MOULDED
FITTINGS" as "CAIALUGUE MOULUEDFILLINGS". Unreliable spec data feeding a FAIL
is worse than no check. There is no OCR engine on this machine anyway
(`winsdk` has no Python 3.14 wheel, Tesseract is not installed).

To read a scanned spec, render it and look at it:
`page.get_pixmap(dpi=110).save(...)` then read the image.

Specs not in the table (P130 appears in the ARN10 line numbers) report NO DATA.
`spec_source_warnings()` warns when a spec PDF on disk has a different revision
suffix than the one transcribed.

## Jordan's DP094 issue-check feedback (2026-08-11)

Four fixes from comments on the 2026-08-10 DP094 marked-up set; all four are
locked in by `test_dp094_feedback`:

- **Split valve prefixes.** The DP094 sheets draw `RG630VB16` as two SHX
  annotations, `RG` and `630VB16`, so every valve read as prefix-less and
  VLV-01 said "no service" against tags that plainly carry one.
  `specs.find_valves()` reunites a prefix-less tag with a service item butted
  against its leading edge (left of a horizontal tag, either end of a vertical
  one, ~2pt gaps measured). VLV-01/02/03 now also carry explicit anchors.
- **Water crossings are not wellsites.** TB-07 counted `CMN271` inside
  `WC-CMN271-3-1` and corridor labels as a drawn wellsite. It now counts only
  bare tags (`BARE_WELL_TAG`), and only those that also appear in corridor
  form on the sheet -- the corroboration is what keeps instrument tags
  (`PIC006`, same 3-letters-3-digits shape) out.
- **Anchors must not latch onto frame furniture.** MAOP-01 details quote the
  calc filename ("...DP094 & IF439 MAOP Review Calculation.xlsx"), and the
  `DP094` token substring-matched the title line, the reference-drawings table
  and the plot-stamp path. MAOP-01 and CMT-01 now set explicit anchors, and
  `markup._locate` is tiered: exact matches anywhere; prefix ("token-")
  and quoted-substring matches only in the drawing body (cy < 0.87h, x0 > 34).
  The title line sits at cy/h 0.908 and the reference table starts at 0.913,
  too close to separate geometrically -- hence explicit anchors for title
  findings rather than a looser band.
- **No standard-notes comparison.** NOTE-02 flagged 11 of 14 sheets for
  legitimate wording; Jordan asked for internal consistency only. NOTE-02 is
  removed along with the Standard Notes tab reader, and NOTE-03 is pure
  majority-vs-minority (the standard-wording override is gone too -- the one
  drawing using the checklist wording IS flagged when the other six agree
  on something else; the finding quotes both wordings so the engineer decides).

Also: the 12 DP442 PDFs vanished from `reference/ARN10 PEFs/` in the
2026-08-10 reference reshuffle, which silently reduced the "14-drawing batch"
tests to 2 drawings. Restored from
`P:\ARN10\F - PROCESS\ARN10-F-PID-01xx ... .pdf` (the un-suffixed originals,
not the `_CP` checkprint copies).

## Lot-on-plan property references look like valve tags (2026-08-13)

The ARN12 DP401 sheets label properties with Queensland lot-on-plan
references beside the boundary: `95DY462` (lot 95, survey plan DY462),
`1RP188524`, `49DY467`. The shape is identical to a valve tag, so CMT-03
fired on all of them. `_valve_like()` now requires the letter group to still
be plausibly a valve kind (starts with V, or bare B/F/G with the V dropped)
before a near-miss is flagged; the RG250B16 catch still fires.

## Valve tag format

`<service><size><type><code>` — `RG125VB16`, `RW400VB17`. Older Origin sheets
omit the service prefix (`125VB17`). Because the tag carries service and size,
VLV-01 and VLV-02 need no spatial reasoning and are fully objective. VLV-03
must guess which line a valve sits on, so it drops to REVIEW whenever more than
one candidate line spec is plausible.

When a tag has no service prefix, infer the service from the valve code
(VB16 gas, VB17 water) before matching it to a line — matching on size alone
picks the wrong line, because gas and water lines of the same size run side by
side at a wellsite. Before any of that, `find_valves()` tries to reunite the
tag with a split-off `RG`/`RW` prefix item (see the DP094 feedback section).

## Markup

`markup.py` writes a copy of each PDF with numbered callouts, sticky notes and
an appended comment sheet. Two things to remember:

- A **FreeText annotation displays its /Contents**. Setting the note text on
  the numbered flag renders the whole note inside the little box. The note goes
  on the rectangle annotation (shown as a popup); the flag gets a title only.
- Annotation rects are in **unrotated** page space. Item positions are in
  displayed space, so multiply by `page.derotation_matrix` before placing.

Findings are anchored by the tags their detail text quotes (`LOCATABLE` in
`markup.py`), so new checks get markup for free as long as their detail names
the tag. `Result.anchors` overrides this when a check knows the position.

**Every comment number must be visible on the drawing** (Jordan, 2026-08-13:
"only two boxes shown on the PDF but 5 comments on the associated sheet").
A finding with no location is flagged in the left margin (`MARGIN_*` in
`markup.py`), never silently dropped to the comment sheet. Checks that FAIL
on something a well-formed pattern cannot re-find must carry their own
anchors: a malformed line number (LN-01), a mistyped valve tag (CMT-03), a
revision row (TB-03/04 anchor on the description text -- a bare "A" matches
everywhere).

Markup must never alter the drawing: the test asserts `page.read_contents()` is
byte-identical. Note `get_text()` is *not* a valid comparison — it picks up the
callout numbers from the FreeText appearance streams.

## The IF439 set cannot be checked (2026-07-31)

All 10 drawings in `reference/ARN10 PEFs/IF439` were plotted **without**
"capture SHX fonts as comments" — zero annotations, so zero readable text. They
are proper vector plots (14k–18k paths) from `pdfplot17.hdi 17.00.171`, a newer
plot driver build than the DP442 set's `17.00.058`, which is probably where the
setting was lost. Fix is `PDFSHX = 1` in AutoCAD, then re-plot.

`EXT-01` FAILs all ten and the CLI prints a batch-level banner naming the
setting. Do not try to work around this with OCR — there is no engine on this
machine and a garbled text layer feeding a FAIL is worse than no check.

## Three kinds of text layer (know which you have)

1. **SHX capture** (`PDFSHX = 1`) — annotations carrying whole strings.
   Authoritative. The DP442 set.
2. **TrueType annotation text** — the drafter uses a TrueType style instead of
   SHX, so AutoCAD embeds real text. **Also authoritative, and arguably better**
   (real text, not annotations). Detected by producer/creator `pdfplot`/
   `AutoCAD` — see `_plotted_by_cad`. Caveat: only the *annotation* text is
   TrueType; the title block **template** (labels, and the CADFILE under the
   border) is still SHX, so the label anchors find nothing even though the
   values are readable. Hence the positional fallbacks in `titleblock.py`
   (`_positional_fallback`, `_positional_revision_history`,
   `_positional_reference_drawings`), keyed off fractional bands that hold on
   both the SHX and TrueType frames.
3. **OCR** — added by Bluebeam/Acrobat/a scanner. Never authoritative; capped
   at REVIEW.

`is_ocr` is (2) vs (3): a word layer is only untrustworthy if something
*recognised* it. Plotted straight from AutoCAD, it is the drawing's own text.

## Drawing number separator

`/` on the drawing, `_` in filenames and some reference tables (Windows forbids
`/`). `ORIGIN_DWG_NO` accepts both; compare with `normalise_drawing_number()`.
Also note `ORIGIN_DWG_NO_LEAD` — the same pattern without the trailing `\b`,
needed when the revision is butted onto the number (`...P03A`); with the `\b`
the match backtracks to `Q-4255-10-DF-030` and silently loses the sheet number.

## OCR text layers (2026-07-31)

Jordan OCR'd the IF439 set and asked for a re-run. The tool now handles it:

- `Sheet.is_ocr` is set when a page has no SHX annotations but >= 100 embedded
  words. `merge_words_into_strings()` rebuilds whole strings from word tokens
  (OCR gives one item per word; every label-driven check needs whole strings).
- The merge gap is deliberately tight (0.55x height, max 9pt) because OCR
  bounding boxes are **inflated** — the drawing number's box overlapped the
  revision's, so a generous gap produced "Q-4255-10-DF-572P01 A". The title
  block parser now extracts the drawing number by regex from the cell for the
  same reason.
- Every FAIL from an OCR sheet is downgraded to REVIEW, in two places:
  the `add` closure in `run_checks`, and `_cap_ocr_findings` at the end of
  `run_batch_checks` (batch checks append Results directly and would otherwise
  bypass the cap).

**Do not relax this cap.** Measured evidence: the combined file contains the
set twice, OCR'd separately, and the two passes disagree on 10-20% of tags —
`P151-4255` vs `P151-4259`, `COM067-3-2` vs `COM061-3-2`, `250-RG-` vs
`250-R0-`. The errors are non-deterministic, so they cannot be corrected for.

## XXXX line-number sequences are a hold, not a defect (2026-08-10)

`LN-01` no longer fails on `125-RG-XXXX-P153-4255`. Jordan assigns the
sequence later from the MAOP calc, so an `XXXX` placeholder is a deliberate
hold and firing on it was noise — it hit 13 of 14 sheets. The count is still
reported in the detail (`n on an XXXX sequence hold.`) so the holds stay
visible; only a **malformed** line number fails now. Note `MAOP-01` compares
with the sequence ignored anyway, so nothing downstream depended on it.

## Continuation references (`CONT-02`, 2026-08-10)

"A line runs off the sheet into drawing X — is there an X in this set?"
`continuation_refs()` finds the pennant flags around the border; the check is
in `content_checks.py`, runs on PDFs and DXFs alike, and is separate from the
DXF-only `CONT-01`, which reads the flag's `DOCNO` attribute and deliberately
tolerates a target outside the run.

**The bands are tight on purpose.** Measured on DP094: flags sit at
`cx/w` ≈ 0.074 (left) and 0.935 (right), `cy/h` ≈ 0.097 (top) and 0.807
(bottom). The `Q-1300-10-DP-0214` standard-detail callouts scattered through
the drawing body come within 5pt of the top band — widen it and every sheet
picks up phantom continuations. Anything below `cy/h` 0.885 is the revision and
reference-drawing tables, not a continuation.

**Typo vs out-of-package.** A target one edit from a drawing in the set FAILs
(`Q-4255-10-DF-22008` where the set holds `...-220008`). A target nothing like
anything in the set is REVIEW — a line may legitimately continue into another
package, as DP094's `Q-4255-10-DF-099` continues into DP442. The near-miss
search **excludes the sheet's own number**: in a sequentially numbered set a
drawing is one character from its neighbours, and "you meant this drawing
itself" is never right. The residual risk is a genuine continuation to the next
package (`...-220017` beside an in-set `...-220016`) reading as a typo; if that
ever fires wrongly, tighten on project code and document type rather than
loosening the distance.

## Continuation-flag flow direction (FLOW-01/02/03, TIP-01, 2026-08-17)

The continuation pennant's **chevron apex points in the direction of flow**:
apex toward the border = out, apex into the body = in. Trunk/corridor lines
use a pennant **pointed at both ends**, and a both-ended pennant always meets
a both-ended pennant on the neighbouring sheet. Measured across every flag in
ARN12 DP401, ARN10 DP442 and DP094; not documented in any of the standards in
`origin standards/` (searched), so the drawings themselves are the authority.

How it is read (`flow_checks.py`): the pennant outline is the thin stroked
path (0.24pt on issued Origin plots; ceiling 0.8 so a ctb-less re-plot that
collapses every weight to 0.72pt still reads) around the flag's text item in
`page.get_drawings()`; two diagonals meeting at an end = a chevron. Only
white strokes are colour-filtered (wipeout edges) — a ctb-less plot comes out
coloured, and revision clouds are curves, which the reader never collects.
The white `fill` paths inside the flag are the wipeout behind the text (a
rectangle drawn as two triangles) — ignore fills or every flag looks
double-ended.
Detail callouts (`Q-1300-10-DP-0214`) that stray into the continuation bands
have no pennant outline, which is what keeps them out of the flow checks.

Matching across a boundary is by the line number drawn along the pennant
(`250-RG-2280` names the same corridor on both sheets — size-service-sequence,
ignore spec and project code, and an `XXXX` sequence is no key), then by the
RAW GAS / RAW WATER label. Head-on contradiction = FAIL; missing counterpart
or single-meets-both-ended = REVIEW. Real defect found: **DF-094 and
DF-095P01 both draw 250-RG-2280 and 125-RW-2416 pointing into their own
sheet** — confirmed by eye, locked in by `test_arn_batch`.

FLOW-03 (a service whose every readable flag points IN has nowhere to go) is
REVIEW, not FAIL — a both-ended flag in the service earns the benefit of the
doubt, and a line can terminate at a facility drawn on the sheet.

TIP-01 (tie-in number reuse) is REVIEW, never FAIL: text alone cannot tell a
doubled-up number from the same physical tie-in drawn on two adjacent sheets
(TIP-CMN296-1 on two DP094 drawings is exactly that). Fires on 1 tag in 119
across the reference sets.

DXF-derived sheets report FLOW-01 NO DATA — direction comes from PDF vector
geometry. If flow checks are ever wanted on DXFs, capture the block insert's
rotation in `BlockRef` and calibrate against a plotted PDF first.

## Jordan's check-list workbook feedback (2026-08-20)

Eight changes from comments Jordan saved into `output/PEFS check list.xlsx`:

- **SP-01 reads all drawing text** now, not just title-block prose. Accepted
  cost: property-owner/locality names (DOUGALL, MEROO, BEALLA...) surface as
  REVIEW — a dictionary cannot know rural Queensland. Do not "fix" that by
  narrowing the scope again.
- **TB-08 is inverted**: the PROJECT NO. box must be EMPTY on Origin
  gathering jobs; populated (e.g. `DP401`) is REVIEW. Fires on real sheets —
  that is intended, not noise.
- **TB-19** requires the derived-from note's number to equal this drawing's
  number minus the P modifier (was a loose startswith; ...-017 would have
  accepted ...-0179). Mismatch is FAIL.
- **REF-01** (batch): a reference-drawings entry naming a sheet in the set
  must carry that sheet's issued number, P modifier included — forgetting to
  update `...-0099` to `...-0099P01` is the mistake it exists for. FAIL.
- **TAG-03** (batch): HPV/LPD/DIP corridor identifiers and crossing tags
  unique. A drain symbol's identifier is the CORRIDOR_ID text *touching* it
  (gap 0.0 measured across ARN12+DP442; threshold DRAIN_ID_GAP=6). Uniqueness
  is per kind — HPV and LPD legitimately share an identifier, two HPVs do
  not. TIP- tags are excluded (TIP-01 owns them). Found real doubles on
  DP442: two HPVs both labelled CMN399-2-1 on PID-0118 (verified by eye),
  PHS039-1-1 twice on PID-0113, PHS062-2-1 on both 0111 and 0112.
- **TAG-04**: the written crossing-type label ("POWERLINE CROSSING") touches
  its tag; prefix must match the type per A-1000-10-DH-009 (WC/RC/HPC/HVC/
  PLC/CC — CC comms/cable added to CROSSING_PREFIXES). Mismatch FAIL.
- **SIZE-01**: OD560 pipe/valves → REVIEW recommending OD630 for ease of
  purchasing. The DP094 set genuinely carries 560 trunk lines, so it fires
  there — intended.
- **Markup draws a one-line summary label** beside each finding's first
  callout (white fill, status-coloured text) so the drawing reads at a glance
  without the comment sheet. PyMuPDF gotcha: `add_freetext_annot` rejects
  `border_color` unless rich_text — don't pass it.

Also: A-1000-10-DH-009 (PEFS standard symbology, now at `reference/`)
officially names the single-pointed continuation pennant "PEFS PAGE CONNECTOR
UNIDIRECTIONAL FLOW" and the both-ended one "...BIDIRECTIONAL FLOW" — the
measured FLOW convention is the documented one.

The hand-verified reference sheet `Q-4300-10-DF-193_1_IFC.pdf` now lives in
`samples/` (ctr-generator moved to `~superseded` 2026-08-20).

## Jordan's combined.pdf feedback, round 2 (2026-08-20)

Jordan marked comments ("JT:") into the staged markup and asked for these;
all locked in by the test suite:

- **Markup is boxes-only**: no appended comment sheet, no on-sheet summary
  text. The full finding lives in each rectangle's comment (popup) metadata;
  the numbered flag and margin stack stay.
- **TB-08 anchors on the PROJECT NO. box itself** (label + value item in the
  frame). Without explicit anchors the markup located the quoted 'DP401' on
  the drawing body's project-number tags. The extraction was verified right —
  the box on the flagged sheets genuinely carries DP401.
- **CONT-02 reshaped**: out-of-package continuations are NOT findings (they
  are counted in the PASS detail); the FAILs are a typo one character from an
  in-set drawing, or an in-set base number missing its issued P modifier.
  Only DF-type targets are continuations (kills the Q-1300-10-DP standard
  callout noise). Set membership falls back to a drawing number found in the
  FILENAME when the frame is unreadable — `_sheet_number()` in
  content_checks, also used by the FLOW pairing — so a frame-less re-plot
  still counts as "in the set" and its neighbours stop failing.
- A title-block number that is not ORIGIN_DWG_NO-shaped ('NTS', grabbed
  positionally from a frame-less plot) is treated as absent by
  `_sheet_number`, TB-19 and BAT-01.
- **SP-01 findings anchor on the suspect words** (explicit rects; the markup
  locator only matches tag-shaped tokens).
- **LN-01 wording**: the XXXX-hold count is its own sentence so it cannot
  read as if the malformed number were the hold.

## All-projects batch hardening (2026-08-20, 46-sheet run)

Running every issued rev A PEFS (ARN05 + ARN10 + ARN12) in one batch exposed
four defects, each verified against a real sheet before fixing:

- **`normalise_drawing_number` now handles a sheet separator followed by a
  modifier**: `030_13P03` == `030/13P03`. Without it FN-02 failed the two
  030-series IF439 sheets on pure separator difference.
- **CONT-02's near-miss typo rule requires a LENGTH-CHANGING edit** (dropped
  or doubled digit, `DF-22008` for `DF-220008`). A same-length substitution
  one edit from an in-set number (DF-083 beside DF-081/085/093) is almost
  always a real neighbouring sheet in another package — every same-length hit
  in the 46-sheet batch was one.
- **`_flag_edge` uses the flag's orientation, not position bands**: a
  vertical pennant always belongs to the top/bottom edge, horizontal to
  left/right. DF-1157P02's bottom-LEFT corner water pennant sat in the left
  band, decoded IN instead of OUT, and manufactured a two-sheet flow
  contradiction with DF-1154P01 (congruent in reality — verified by eye).
- **Duplicate checks ignore degenerate items** (`_drawn`): the plot driver
  parks ~1pt SHX annotations at the page corner (a second `TIP-CMN295-1` at
  (0,841)-(1,842) on issued DP094 sheets) which read as phantom same-sheet
  doubles.

Confirmed genuine on the issued fleet (see `output/All rev A PEFs check
2026-08-20/`): the DF-094↔095P01 flow contradiction; PLC-CNH002-2-1 labelled
WATER CROSSING (TAG-04's first live catch, IF439 PID-0065); DF-1157P02
referencing 1158P02 where the set issues P03; transposed RW/VB16 valves on
IF439 0063/0065 + DP442 0114; WELL-01 CHN001-for-CNH001 on 0061; the DP442
HPV identifier doubles; TIP-CMN296-1; and the 22008 continuation typo was
fixed before the rev A issue (absent from issued 0043). Unknown crossing
prefixes PIT- and SB- (spec break?) surfaced on IF439 — ask Jordan whether to
whitelist.

## The ARN12 test pair (2026-08-20)

Jordan seeded a test: an ORN150 tie-in added across DF-177P01/DF-178P01
(`reference/ARN12 test/`). The issued test PDFs were plotted from AutoCAD
2025 **without PDFSHX** — unreadable, EXT-01 FAILed them as designed. Worked
around by re-plotting locally from the P:\ DWGs (copies in
`reference/ARN12 test/dwgs`) with the batch exporter; the replots carry full
SHX capture but no title block (this machine lacks the `A1_OE_LB_ARN.dwg`
xref), so frame/identity checks degrade to NO DATA on them and CONT/FLOW
cross-sheet pairing to a replot breaks (no drawing number to key on). Checked
as a staged batch: replots + the four untouched DP401 sheets.

Seeded defects caught: TIP-ORN150-1 on both the new 0017 and DF-220004
(TIP-01), HPV ORN150-1-1 / ORN150-2-1 duplicated across sheets (TAG-03), and
the re-typed 0018 note saying "HAZARDOUS AREAS" against "AREA" on 0021/0022
(NOTE-03). The new ORN150 continuation pennant pair reads congruent
(0018 out / 0017 in) — no flow defect seeded.

## Sources of truth beyond the drawings

- `reference/ARN10 Project Documents/ARN10-P-CALC-0029 ... MAOP Review
  Calculation.xlsx` — corridor to line number, read by `maop.py`. Corridor ID
  is the join key. **`maop.load()` takes whatever copy it finds and prints the
  filename — it does not know which revision is current.** On 2026-08-10 Rev B
  was deleted in a reference reshuffle and the run silently fell back to Rev A,
  which still specifies `P157` on corridors Rev B moved to `P153`; that alone
  produced nine MAOP-01 FAILs against drawings that were right. Always read the
  revision in the banner line before believing a MAOP finding. **Sequence and trailing project code are ignored**: the calc
  writes `XXXX`, and Jordan changes the project code to the drawing's area
  number.
- `reference/ARN10 Project Documents/DP442 IFR Checkprint ECT Check.pdf` —
  client comments (Bluebeam; reviewers KC, RAT, Ewan Thiele). The FreeText
  annotations are the comments; the ~300 Square annotations per page are just
  the SHX capture. Implemented ones are in `client_checks.py`; the rest are
  listed at the bottom of that module as needing a human.

## Status

**67 check codes** (NOTE-02 removed 2026-08-11; FLOW-01/02/03 + TIP-01 added
2026-08-17; TAG-03/04, SIZE-01, REF-01 added 2026-08-20; 60 apply to a PDF
run, 7 are DXF-only: DXF-01..06 + CONT-01) over
frame/identity, drawing body, PDF metadata, pipe specs and valves, geometry,
continuation flow direction, notes, client checkprint comments, the MAOP
review calculation, and the batch as a whole — plus marked-up PDF output.
The authoritative list is generated from the source by
`python tools/export_check_list.py` — do not hand-count.

On the 14 readable ARN05/ARN10 PEFS: **25 FAILs, 36 REVIEWs**, all confirmed
genuine by hand. Headline finds:

- `RG125VB17`/`RW125VB16` transposed on DF-094 — gas valve on water and vice
  versa, independently confirmed by the client's own VB32/VB17 comments
- `RG250B16` on DF-094 — the "VB Typo" RAT flagged, caught by CMT-03
- all 12 DP442 drawings name the DP "HILLSIDE WEST", not "HILLSIDE WEST PHASE 3"
- the LPD collection note worded two ways across the set (NOTE-03; the odd
  one out is flagged, whichever wording it is)
- "HPV LOCATED" vs "HPV TO BE LOCATED" split across the set
- `XXXX` line-number placeholders on 9 drawings
- duplicate line sequences (2374, 2244, 2408) on 5 drawings
- DF-094/DF-095P01 continuation pennants for 250-RG-2280 and 125-RW-2416
  contradict each other (both sheets show the flow arriving from the other)

`origin standards/` (added by Jordan 2026-08-17) holds the Origin pipe spec
sheets and pipeline standards for reference. The HDPE pipe specs folder has
sheets for P123-P130 and P180 — P130's sheet is there if its transcription is
ever wanted in `specs.py` (currently NO DATA).

Remaining ideas are under "Not yet covered" in `README.md`. The three geometry
checks that were measured and rejected are documented at the bottom of
`geometry_checks.py` — don't re-attempt them from PDF.

## DXF front end (2026-08-03)

`dxf_extract.py` reads a DXF into the **same `Sheet` shape** the PDF reader
produces, so every existing check runs unchanged. Extra structure hangs off
`Sheet.dxf` (blocks, segments, layers) and `Sheet.transform`.

**Coordinates.** Three chained spaces:
`model --viewport--> paper --plot scale + origin--> mm --> PDF points`.
The viewport's `view_target_point` is the model point at its centre (NOT
`view_center_point`, which is the offset from it and reads (0,0) here);
`height / view_height` is the scale. `scale_numerator/denominator` plus
`plot_origin_*_offset` give paper→mm. Verified to 7pt against the plotted PDF,
which is what lets DXF findings be marked up on the PDF with no further
transform.

**Block naming varies across ~3 lineages** — a flow arrow is `tag_flowa`,
`Flow Arrow`, or `PEF_STAGE-4_OVERALL$0$FLOW`. `BLOCK_KINDS` maps normalised
names to semantic kinds; **normalise the pattern as well as the name** or
`val_ball` will not match `Val_ball`. `DXF-01` reports what was not recognised.

**PDF-only checks are forced to N/A on a DXF** (`PDF_ONLY` in `run_checks`):
filename, CADFILE stamp, layout name, plot dates. A DXF is the source, not the
deliverable — run the tool on both.

**Markup target**: `markup_target()` looks for a same-named PDF beside the DXF,
then in a sibling `pdfs/` folder (where the batch exporter puts them).

**DXF-06 (connectivity) is informational and must stay that way** until there
is a drawing with a known gap to calibrate against — 16-44% of endpoints read
as loose on every sheet in the IF439 set with no evidence of a real defect.

### Traps hit while building this, worth not repeating

- **Bash heredocs mangle regex escapes.** `\b` written through `<<'PY'` became a
  literal 0x08 byte in `checks.py`, so `re.search(r"\bPEFS?\b", ...)` silently
  never matched and `grep` rendered it invisibly. Use the Edit tool for anything
  containing backslash escapes, or write bytes explicitly.
- **Same-second writes leave stale bytecode.** Editing a module and re-running
  within the same second can execute the old `.pyc`. If a change appears to have
  no effect, `find . -name __pycache__ -type d -exec rm -rf {} +` first.
- **PyMuPDF segfaults if Annot objects outlive their page.** Read `.rect` and
  `.info` out inside the loop; never keep the Annot itself.
- **MTEXT arrives as one long string**, so the synthesized text box has to be
  clamped to the sheet or note blocks produce anchors running off the page.

## DXF markup (`dxf_markup.py`)

Writes findings into a **copy** of the DXF. Paper space, on `CHECK-FAIL` /
`CHECK-REVIEW` / `CHECK-INFO` layers, plus a `CHECK COMMENTS` layout holding
the schedule. `_purge_previous()` clears prior markup so re-running does not
stack up.

Paper space rather than model space on purpose: page coordinates map straight
to paper via `PageTransform.page_to_paper`, so a callout lands exactly where
the PDF one does, and there is no model/paper ambiguity for title-block
findings. Note page y runs *down* the sheet and paper y runs *up*, so the box
top and bottom swap in `_draw_callout`.

Keep the on-sheet label to one line (`NOTE_MAX_CHARS`). Writing the full detail
there buries the drawing — it was tried, and the comment text ran clean across
the pipework. Full text lives in the schedule layout.

MTEXT treats `\` `{` `}` as formatting codes, so anything written into MTEXT
must go through `_escape()`.
