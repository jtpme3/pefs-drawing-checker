"""Validation of the checker against a known drawing and mutations of it.

Run with:  python tests/test_checks.py

The reference sheet is Q-4300-10-DF-193 Rev 1 (Origin ARN05 batch), checked by
hand against the PDF.  Mutants are generated from it to prove the checks fire
when they should -- in particular that a drawing plotted *without* SHX capture
fails loudly instead of passing by default.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from drawing_checker.checks import FAIL, NA, PASS, REVIEW, SKIPPED, run_checks
from drawing_checker.content_checks import BatchContext, run_batch_checks
from drawing_checker.extract import dedupe_shx_repeat, load_sheets
from drawing_checker.report import SheetReport
from drawing_checker.spelling import BatchSpeller, prose_fragments
from drawing_checker.titleblock import parse as parse_title_block

# The hand-verified reference sheet. Originally lived in the ctr-generator
# project; that moved to ~superseded on 2026-08-20, so the copy in samples/
# is now the canonical one.
REFERENCE = Path(__file__).resolve().parent.parent / "samples" / "Q-4300-10-DF-193_1_IFC.pdf"

failures: list[str] = []


def check(condition: bool, description: str) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'}  {description}")
    if not condition:
        failures.append(description)


def status_of(results, code: str) -> str:
    return next((r.status for r in results if r.code == code), "<missing>")


def run(pdf: Path):
    """Check one sheet, with a speller built from that sheet alone."""
    sheet = load_sheets(pdf)[0]
    tb = parse_title_block(sheet)
    speller = BatchSpeller()
    speller.add(sheet.label, [dedupe_shx_repeat(f) for f in prose_fragments(sheet, tb)])
    context = BatchContext(speller=speller, key=sheet.label)
    return sheet, tb, run_checks(sheet, tb, context)


def run_batch(pdfs: list[Path], maop=None) -> list[SheetReport]:
    """Check a set of sheets together, so batch checks and the batch speller run."""
    speller = BatchSpeller()
    loaded = []
    for pdf in pdfs:
        for sheet in load_sheets(pdf):  # multi-page sets are one sheet per page
            tb = parse_title_block(sheet)
            loaded.append((pdf, sheet, tb))
            speller.add(
                sheet.label, [dedupe_shx_repeat(f) for f in prose_fragments(sheet, tb)]
            )

    reports, entries = [], []
    for pdf, sheet, tb in loaded:
        report = SheetReport(
            sheet.label, pdf.name, tb.drawing_number, tb.revision,
            run_checks(sheet, tb,
                       BatchContext(speller=speller, key=sheet.label, maop=maop)),
        )
        reports.append(report)
        entries.append((report, sheet, tb))
    run_batch_checks(entries)
    return reports


def strip_annotations(src: Path, dst: Path) -> None:
    """Re-plot without 'capture SHX fonts as comments', in effect."""
    doc = fitz.open(src)
    page = doc[0]
    for annot in list(page.annots() or []):
        page.delete_annot(annot)
    doc.save(dst)
    doc.close()


def test_reference_extraction() -> None:
    print("\nreference drawing - field extraction")
    _, tb, _ = run(REFERENCE)
    check(tb.drawing_number == "Q-4300-10-DF-193", "drawing number reads Q-4300-10-DF-193")
    check(tb.revision == "1", "revision reads 1")
    check(tb.project_no == "DP341", "project number reads DP341")
    check(len(tb.title_lines) == 4, f"4 title lines (got {len(tb.title_lines)})")
    check(
        tb.title_lines[3] == "PROCESS ENGINEERING FLOW SCHEMATIC - DP341 MAY PARK",
        "fourth title line is the PEFS line",
    )
    check(
        [s.initials for s in tb.signoffs] == ["CDF", "BGD", "KC", "RAT", "BGD", "JAG"],
        "six sign-off initials read in order",
    )
    check(
        all(s.date == "12.09.2025" for s in tb.signoffs),
        "all sign-off dates read as 12.09.2025",
    )
    check(
        [r.revision for r in tb.revision_history] == ["1", "0", "A"],
        "revision history reads 1 / 0 / A newest first",
    )
    check(
        tb.revision_history[0].description
        == "ISSUED FOR CONSTRUCTION - PCN-EST-3296 INCORPORATED",
        "latest revision description read in full",
    )
    check(
        [n for n, _ in tb.reference_drawings]
        == ["Q-4300-10-DF-188", "A-1000-10-DH-009"],
        "two reference drawings read, address block excluded",
    )
    check(
        tb.cadfile_border.upper().endswith(("DWG", "BEN")) and ".DWG" in tb.cadfile_border.upper(),
        "CADFILE path recovered from under the border",
    )


def test_reference_checks() -> None:
    print("\nreference drawing - check results")
    _, _, results = run(REFERENCE)
    check(status_of(results, "EXT-01") == PASS, "EXT-01 text layer present")
    for code in ["FN-01", "FN-02", "FN-03", "TB-01", "TB-02", "TB-03", "TB-04",
                 "TB-05", "TB-06", "TB-07", "TB-10", "TB-11", "TB-12", "TB-13",
                 "TB-14"]:
        check(status_of(results, code) == PASS, f"{code} passes on a good drawing")
    check(
        not [r for r in results if r.status == FAIL],
        "no FAILs raised on a correctly issued drawing",
    )


def test_mutants(tmp: Path) -> None:
    print("\nmutants")

    # The important one: a real drawing sheet the tool cannot read.
    blind = tmp / "Q-4300-10-DF-193_1_IFC.pdf"
    strip_annotations(REFERENCE, blind)
    _, _, results = run(blind)
    check(
        status_of(results, "EXT-01") == FAIL,
        "SHX capture off -> EXT-01 FAILs (does not silently pass)",
    )
    check(
        SKIPPED not in {r.status for r in results},
        "an unreadable drawing sheet is never quietly skipped",
    )

    # Filename revision disagrees with the title block.
    wrong_rev = tmp / "Q-4300-10-DF-193_0_IFC.pdf"
    shutil.copy(REFERENCE, wrong_rev)
    _, _, results = run(wrong_rev)
    check(status_of(results, "FN-03") == FAIL, "filename rev 0 vs frame rev 1 -> FN-03 FAIL")
    check(status_of(results, "FN-02") == PASS, "...and the drawing number still passes")

    # Filename drawing number disagrees with the title block.
    wrong_no = tmp / "Q-4300-10-DF-999_1_IFC.pdf"
    shutil.copy(REFERENCE, wrong_no)
    _, _, results = run(wrong_no)
    check(status_of(results, "FN-02") == FAIL, "filename DF-999 vs frame DF-193 -> FN-02 FAIL")

    # Filename follows no known convention.
    junk = tmp / "PEFS final for issue (2).pdf"
    shutil.copy(REFERENCE, junk)
    _, _, results = run(junk)
    check(status_of(results, "FN-01") == FAIL, "unrecognised filename -> FN-01 FAIL")

    # A non-drawing PDF is skipped, not failed.
    spec = REFERENCE.parent / "Q-1000-95-AP-184_2_IFU.pdf"
    if spec.exists():
        _, _, results = run(spec)
        check(
            status_of(results, "EXT-01") == SKIPPED
            or status_of(results, "TB-00") == SKIPPED,
            "a specification PDF is skipped, not failed",
        )


ARN_SETS = [
    Path(__file__).resolve().parent.parent / "reference" / "AN05 PEFs",
    Path(__file__).resolve().parent.parent / "reference" / "ARN10 PEFs",
]


def status_in(reports, label_fragment: str, code: str) -> str:
    for report in reports:
        if label_fragment in report.label:
            return status_of(report.results, code)
    return "<no such drawing>"


def detail_in(reports, label_fragment: str, code: str) -> str:
    for report in reports:
        if label_fragment in report.label:
            for result in report.results:
                if result.code == code:
                    return result.detail
    return ""


def test_arn_batch() -> None:
    """The real ARN05 + ARN10 issue set, checked as one batch."""
    pdfs = [p for folder in ARN_SETS for p in sorted(folder.glob("*.pdf"))]
    if not pdfs:
        print("\nARN drawing set not present - skipping batch tests")
        return
    print(f"\nARN batch ({len(pdfs)} drawings) - checks verified by hand")
    reports = run_batch(pdfs)

    check(len(reports) == 14, f"all 14 drawings read (got {len(reports)})")
    check(
        all(
            res.status != "NO DATA"
            for r in reports for res in r.results
            if res.code.startswith(("TB-", "FN-"))
        ),
        "no drawing frame field went unread on any drawing",
    )

    # An XXXX sequence is a hold the drafter clears later from the MAOP calc,
    # not a defect, so it is reported in the detail but must not fail.
    check(status_in(reports, "PID-0108", "LN-01") == PASS,
          "PID-0108 XXXX placeholders are a hold -> LN-01 PASS")
    check("XXXX sequence hold" in detail_in(reports, "PID-0108", "LN-01"),
          "...and the held line numbers are still counted in the detail")
    check(status_in(reports, "PID-0107", "LN-01") == PASS,
          "PID-0107 has all line numbers assigned -> LN-01 PASS")

    # "APPROVED OPERATION DRAWING" should read OPERATIONAL.
    check(status_in(reports, "PID-0109", "TB-19") == FAIL,
          "PID-0109 'APPROVED OPERATION DRAWING' -> TB-19 FAIL")
    check(status_in(reports, "PID-0107", "TB-19") == PASS,
          "PID-0107 correct derived-from wording -> TB-19 PASS")

    # PSH059 / PSH079 are transpositions of PHS059 / PHS079.
    check(status_in(reports, "PID-0110", "WELL-01") == FAIL,
          "PID-0110 PSH059/PSH079 transposition -> WELL-01 FAIL")
    check(status_in(reports, "PID-0107", "WELL-01") == PASS,
          "PID-0107 spells PHS079 correctly -> WELL-01 PASS (not blamed)")
    check(status_in(reports, "PID-0118", "WELL-01") == PASS,
          "PID-0118 CNH001 is a real wellsite, not a typo -> WELL-01 PASS")

    # Filename hygiene.
    check(status_in(reports, "PID-0117", "FN-05") == REVIEW,
          "PID-0117 'PHS022. CMN351' full stop in filename -> FN-05 REVIEW")

    # Every drawing was plotted on or about its revision date.
    check(
        all(status_of(r.results, "PLT-01") == PASS for r in reports),
        "all drawings plotted on/after their revision date -> PLT-01 PASS",
    )
    check(
        all(status_of(r.results, "PLT-02") == PASS for r in reports),
        "all drawings came straight from the AutoCAD plotter -> PLT-02 PASS",
    )
    check(
        all(status_of(r.results, "SP-01") == PASS for r in reports),
        "spell check is quiet on all 14 real drawings (no false positives)",
    )
    # Rev 0A carries the Rev 0 signatures in the title block, which is correct;
    # Rev A drawings are unsigned, which is also correct.
    check(status_in(reports, "PID-0108", "TB-18") == PASS,
          "PID-0108 rev 0A signatures match its Rev 0 row -> TB-18 PASS")
    check(status_in(reports, "PID-0107", "TB-18") == NA,
          "PID-0107 rev A is unsigned with no Rev 0 row -> TB-18 N/A")

    # -- pipe specification and valve checks --------------------------
    # RG125VB17 / RW125VB16 are transposed: VB16 is a gas valve (P151/P153),
    # VB17 a water valve (P126/P128).
    check(status_in(reports, "PID-0114", "VLV-01") == FAIL,
          "PID-0114 transposed gas/water valve codes -> VLV-01 FAIL")
    check(status_in(reports, "PID-0114", "VLV-03") == FAIL,
          "PID-0114 valves not permitted by their line's spec -> VLV-03 FAIL")
    check(status_in(reports, "PID-0113", "VLV-01") == PASS,
          "PID-0113 RG...VB16 / RW...VB17 correct -> VLV-01 PASS")
    # VB32 near both P126 and P130 lines: association is a guess, so REVIEW.
    check(status_in(reports, "PID-0112", "VLV-03") == REVIEW,
          "PID-0112 ambiguous VB32 line association -> VLV-03 REVIEW")
    check(
        all(status_of(r.results, "VLV-02") == PASS for r in reports),
        "all valve sizes within their permitted range -> VLV-02 PASS",
    )
    check(
        all(status_of(r.results, "SPEC-01") == PASS for r in reports),
        "every line's service matches its spec -> SPEC-01 PASS",
    )
    # 630-RW on P150, whose standard range stops at 450.
    check(status_in(reports, "PID-0118", "SPEC-02") == REVIEW,
          "PID-0118 OD630 on P150 (range 20-450) -> SPEC-02 REVIEW")

    # -- geometry ------------------------------------------------------
    check(status_in(reports, "PID-0108", "GEO-01") == PASS,
          "PID-0108 rev 0A revision markers found -> GEO-01 PASS")
    check(status_in(reports, "PID-0115", "GEO-01") == PASS,
          "PID-0115 P revision red clouds found -> GEO-01 PASS")
    check(
        all(status_of(r.results, "GEO-02") == PASS for r in reports),
        "all sheets use more than one stroke weight -> GEO-02 PASS",
    )

    # -- continuation flow direction ----------------------------------
    # DF-094 and DF-095P01 both draw the pennants for 250-RG-2280 and
    # 125-RW-2416 pointing INTO their own sheet -- each claims the flow
    # arrives from the other, so one pair is backwards. Confirmed by eye
    # on both PDFs (chevrons face each other across the shared border).
    check(status_in(reports, "PID-0114", "FLOW-02") == FAIL,
          "DF-094/DF-095P01 contradictory pennants -> FLOW-02 FAIL")
    check(status_in(reports, "PID-0115", "FLOW-02") == FAIL,
          "...and the FAIL appears on both sheets of the boundary")
    for key in ("250-RG-2280", "125-RW-2416"):
        check(key in detail_in(reports, "PID-0114", "FLOW-02"),
              f"...naming line {key}")
    check(
        all(status_of(r.results, "FLOW-02") in (PASS, NA)
            for r in reports if "PID-0114" not in r.label and "PID-0115" not in r.label),
        "FLOW-02 quiet on every other boundary in the 14-drawing batch",
    )
    check(
        all(status_of(r.results, "FLOW-01") in (PASS, NA) for r in reports),
        "every continuation flag in the batch shows a direction -> FLOW-01 quiet",
    )
    check(
        all(status_of(r.results, "FLOW-03") in (PASS, NA) for r in reports),
        "every service has a flow path off its sheet -> FLOW-03 quiet",
    )
    check(
        all(status_of(r.results, "TIP-01") in (PASS, NA) for r in reports),
        "no tie-in number reused in the ARN05+ARN10 batch -> TIP-01 quiet",
    )

    # -- duplicate asset identifiers (Jordan, 2026-08-20) --------------
    # Confirmed by eye: PID-0118 draws two separate HPV symbols both
    # labelled CMN399-2-1 (both in 0A clouds); PID-0113 doubles PHS039-1-1;
    # PID-0111/0112 share HPV PHS062-2-1 across the boundary.
    check(status_in(reports, "PID-0118", "TAG-03") == REVIEW,
          "PID-0118 two HPVs labelled CMN399-2-1 -> TAG-03 REVIEW")
    check("CMN399-2-1" in detail_in(reports, "PID-0118", "TAG-03"),
          "...and the detail names the doubled identifier")
    check(status_in(reports, "PID-0113", "TAG-03") == REVIEW,
          "PID-0113 HPV PHS039-1-1 drawn twice -> TAG-03 REVIEW")
    check(status_in(reports, "PID-0111", "TAG-03") == REVIEW
          and status_in(reports, "PID-0112", "TAG-03") == REVIEW,
          "HPV PHS062-2-1 on both PID-0111 and PID-0112 -> TAG-03 REVIEW")
    check(
        all(status_of(r.results, "TAG-04") in (PASS, NA) for r in reports),
        "every crossing tag prefix matches its written type -> TAG-04 quiet",
    )
    check(
        all(status_of(r.results, "REF-01") in (PASS, NA) for r in reports),
        "reference tables carry the in-set numbers -> REF-01 quiet",
    )


def test_client_and_note_checks() -> None:
    """Checks added from the DP442 client checkprint, and the note checks."""
    pdfs = [p for folder in ARN_SETS for p in sorted(folder.glob("*.pdf"))]
    if not pdfs:
        print("\nARN drawing set not present - skipping")
        return
    print("\nclient checkprint comments and note consistency")
    reports = run_batch(pdfs)

    # KC: DP442's name is "HILLSIDE WEST PHASE 3"; the drawings say
    # "HILLSIDE WEST". The client noted it applies throughout.
    dp442 = [r for r in reports if "DP442" in r.label]
    check(all(status_of(r.results, "CMT-01") == FAIL for r in dp442),
          f"all {len(dp442)} DP442 drawings flagged for the DP name -> CMT-01 FAIL")

    # RAT: '"VB" Typo.' -- RG250B16 is missing its V.
    check(status_in(reports, "PID-0114", "CMT-03") == FAIL,
          "PID-0114 RG250B16 mistyped valve tag -> CMT-03 FAIL")
    check(status_in(reports, "PID-0113", "CMT-03") == PASS,
          "PID-0113 valve tags well formed -> CMT-03 PASS")

    # KC: "is this number 2374 accidentally duplicated?"
    check(status_in(reports, "PID-0118", "LN-03") == REVIEW,
          "PID-0118 sequence 2374 on two lines -> LN-03 REVIEW")

    # Notes are checked for internal consistency only -- comparison against a
    # standard notes list (the old NOTE-02) flagged 11 of 14 sheets and was
    # dropped as noise on Jordan's instruction (2026-08-11).
    check(all(status_of(r.results, "NOTE-02") == "<missing>" for r in reports),
          "NOTE-02 (standard-wording comparison) no longer runs")

    # "HPV LOCATED..." vs "HPV TO BE LOCATED..." across the set.
    check(status_in(reports, "PID-0109", "NOTE-03") == REVIEW,
          "PID-0109 note wording differs from the set -> NOTE-03 REVIEW")
    # PID-0107 words the LPD note differently from the other six drawings, so
    # under the consistency-only rule it is the odd one out and gets flagged --
    # even though its wording happens to be the checklist's standard one.
    detail_0107 = next(
        (r.detail for r in reports if "PID-0107" in r.label
         for r in [next(x for x in r.results if x.code == "NOTE-03")]),
        "",
    )
    check("LPD" in detail_0107.upper(),
          "PID-0107 flagged as the minority wording of the LPD note")

    # DELETED placeholders are not orphan notes.
    for report in reports:
        detail = next(
            (r.detail for r in report.results if r.code == "NOTE-01"), ""
        )
        if "DELETED" in detail.upper():
            check(False, f"{report.label[:24]}: DELETED note treated as an orphan")
            break
    else:
        check(True, "DELETED placeholder notes are not reported as orphans")


def test_maop() -> None:
    """Line numbers against the MAOP review calculation."""
    from drawing_checker.maop import load as load_maop

    print("\nMAOP review cross-check")
    reference = Path(__file__).resolve().parent.parent / "reference"
    dataset = load_maop(reference)
    check(dataset.loaded, f"MAOP calculation loaded ({len(dataset.by_corridor)} corridors)")
    if not dataset.loaded:
        return
    check(dataset.expectations("CNH001-2") != [],
          "CNH001-2 present in the calculation")
    keys = {e.key for e in dataset.expectations("CNH001-2")}
    check(("900", "RG", "P151") in keys and ("630", "RW", "P130") in keys,
          "CNH001-2 expects 900-RG-P151 and 630-RW-P130")

    pdfs = [p for folder in ARN_SETS for p in sorted(folder.glob("*.pdf"))]
    if not pdfs:
        return
    reports = run_batch(pdfs, maop=dataset)
    # DF-099 is the only readable drawing whose corridors are in the calc.
    check(status_in(reports, "PID-0118", "MAOP-01") == PASS,
          "PID-0118 line numbers agree with the MAOP calculation -> MAOP-01 PASS")
    others = [r for r in reports if "PID-0118" not in r.label]
    check(all(status_of(r.results, "MAOP-01") == NA for r in others),
          "drawings whose corridors are not in the calc -> MAOP-01 N/A")


IF439 = Path(__file__).resolve().parent.parent / "reference" / "ARN10 PEFs" / "IF439"


def test_unreadable_set() -> None:
    """The IF439 set was plotted without SHX capture and cannot be checked."""
    print("\nIF439 set (plotted without SHX capture)")
    pdfs = [p for p in sorted(IF439.glob("*.pdf")) if "OCR" not in p.name.upper()]
    if not pdfs:
        print("  IF439 set not present - skipping")
        return
    reports = run_batch(pdfs)
    check(len(reports) == 10, f"all 10 IF439 drawings read as files (got {len(reports)})")
    check(all(status_of(r.results, "EXT-01") == FAIL for r in reports),
          "every IF439 drawing -> EXT-01 FAIL (no readable text)")
    check(all(not r.skipped for r in reports),
          "none quietly skipped - they are drawings, just unreadable")
    detail = reports[0].results[0].detail
    check("capture SHX fonts as comments" in detail,
          "the finding names the AutoCAD option to switch on")


def test_ocr_layer() -> None:
    """An OCR'd copy is readable, but nothing it says may be asserted as fact."""
    print("\nOCR'd copy of the IF439 set")
    ocr = next((p for p in IF439.glob("*.pdf") if "OCR" in p.name.upper()), None)
    if ocr is None:
        print("  OCR'd PDF not present - skipping")
        return

    sheets = load_sheets(ocr)
    check(all(s.is_ocr for s in sheets), "every page detected as an OCR text layer")
    check(all(s.has_text_layer for s in sheets), "the OCR text layer is readable")

    # Word tokens must be merged back into strings, or no label-driven check
    # can find anything.
    page2 = sheets[1]
    check(any(i.text.strip() == "DRAWING NO." for i in page2.items),
          "word tokens merged back into the 'DRAWING NO.' label")

    reports = run_batch([ocr])
    check(any(r.drawing_number for r in reports),
          "at least some drawing numbers recovered from the OCR text")
    check(all(status_of(r.results, "EXT-02") == REVIEW for r in reports),
          "EXT-02 flags every OCR page as non-authoritative")
    check(
        not [res for r in reports for res in r.results if res.status == FAIL],
        "no check asserts a FAIL from OCR-derived text",
    )
    ocr_notes = [
        res for r in reports for res in r.results
        if res.status == REVIEW and "OCR text layer" in res.detail
    ]
    check(bool(ocr_notes),
          f"downgraded findings say why ({len(ocr_notes)} carry the caveat)")


def test_specs() -> None:
    """The transcribed pipe specs, and the guard against them going stale."""
    from drawing_checker.specs import SPECS, VALVE_SERVICE, spec_source_warnings

    print("\npipe specifications (transcribed from reference/)")
    check(SPECS["P151"].service == "gas" and SPECS["P151"].sdr == "21",
          "P151 is PE100 SDR 21 gas")
    check(SPECS["P128"].service == "water" and SPECS["P128"].sdr == "11",
          "P128 is PE100 SDR 11 water")
    check(VALVE_SERVICE["VB16"] == {"gas"}, "VB16 is a gas-only valve")
    check(VALVE_SERVICE["VB17"] == {"water"}, "VB17 is a water-only valve")
    check(SPECS["P126"].valve("VB17").max_size == 400,
          "P126 VB17 tops out at OD400")
    check(SPECS["P151"].valve("VB16").max_size == 630,
          "P151 VB16 tops out at OD630")
    check(SPECS["P150"].special_order_above == 450,
          "P150 is special order above OD450")

    reference = Path(__file__).resolve().parent.parent / "reference"
    check(spec_source_warnings(reference) == [],
          "every transcribed spec matches the revision on disk")


def test_markup(tmp: Path) -> None:
    """Findings are boxed on the drawing, full text in each box's comment.

    Jordan (2026-08-20): no appended comment sheet and no summary text drawn
    on the page -- the rectangles' popup metadata carries everything.
    """
    from drawing_checker.markup import mark_up

    print("\nPDF markup")
    pdfs = [p for folder in ARN_SETS for p in sorted(folder.glob("*.pdf"))]
    if not pdfs:
        print("  ARN drawing set not present - skipping")
        return
    reports = run_batch(pdfs)
    target = next(r for r in reports if "PID-0114" in r.label)
    sheet = load_sheets(next(p for p in pdfs if "PID-0114" in p.name))[0]

    out, placed, total = mark_up(target, sheet, tmp / "marked.pdf")
    check(out.exists(), "marked-up PDF written")
    check(placed == total and total > 0,
          f"every finding pinned to the drawing ({placed}/{total})")
    codes = {r.code for r in target.results
             if r.status in ("FAIL", "REVIEW", "NO DATA")}

    doc = fitz.open(out)
    try:
        check(doc.page_count == 1, "no comment sheet appended (Jordan 2026-08-20)")
        # Read annotation fields out immediately -- an Annot held past other
        # document operations comes unbound (see CLAUDE.md).
        annots = [
            (a.type[1], (a.info or {}).get("title", ""),
             (a.info or {}).get("content", "") or "")
            for a in (doc[0].annots() or [])
        ]
        original = fitz.open(sheet.pdf_path)
        before = len(list(original[0].annots() or []))
        original.close()
        check(len(annots) > before, "annotations added over the original drawing")
        # Every finding's full text lives in a rectangle's comment metadata.
        box_notes = " ".join(
            content for kind, title, content in annots
            if kind == "Square" and title.startswith("Check ")
        )
        for code in sorted(codes):
            check(code in box_notes,
                  f"a callout box carries the {code} finding in its comment")
        # No text drawn onto the page: the only FreeText annots are the small
        # numbered flags, never a summary strip.
        check(
            all(len(content) <= 4
                for kind, _, content in annots if kind == "FreeText"),
            "no summary text drawn on the sheet (numbered flags only)",
        )
        # The drawing itself must be untouched -- the markup is annotations
        # layered over it, so the page content stream must be byte-identical.
        # (get_text() would differ, because it picks up the callout numbers
        # from the FreeText annotations' appearance streams.)
        original = fitz.open(sheet.pdf_path)
        try:
            check(doc[0].read_contents() == original[0].read_contents(),
                  "the drawing's own content stream is byte-identical")
        finally:
            original.close()
    finally:
        doc.close()


def test_spelling() -> None:
    print("\nspell check - seeded typos")
    speller = BatchSpeller()
    speller.add("clean", [
        "PROCESS ENGINEERING FLOW SCHEMATIC - DP442 HILLSIDE WEST",
        "STANDARD SYMBOLOGY AND GENERAL NOTES - PEFS",
        "LPD COLLECTION LOCATED AT TRUE LOW POINT. ABOVE GROUND LPD RISER AND "
        "TANK FACILITIES LOCATED OUTSIDE OF LEASE AREA ON RIGHT OF WAY (ROW).",
    ])
    speller.add("typos", [
        "PROCESS ENGINEERNG FLOW SCHEMATIC - DP442 HILLSIDE WEST",
        "STANDARD SYMOLOGY AND GENERAL NOTES - PEFS",
        "LPD COLLECTION LOCATED AT TRUE LOW POITN. ABOVE GROUND LPD RISER AND "
        "TANK FACILITES LOCATED OUTSIDE OF LEASE AREA ON RIGHT OF WAY (ROW).",
    ])
    found = {m.word: m.suggestion for m in speller.check("typos")}
    for wrong, right in [("ENGINEERNG", "ENGINEERING"), ("SYMOLOGY", "SYMBOLOGY"),
                         ("POITN", "POINT"), ("FACILITES", "FACILITIES")]:
        check(found.get(wrong) == right, f"'{wrong}' flagged as '{right}'")
    check(not speller.check("clean"), "no flags on correctly spelled prose")


DP094 = Path(__file__).resolve().parent.parent / "reference" / "ARN10 PEFs" / "DP094 PEFs"


def test_continuation_targets() -> None:
    """CONT-02 on the DP094 set, whose defects were confirmed by hand.

    DF-220007's top flag reads Q-4255-10-DF-22008 -- five digits where the
    sheet below it has six -- and DF-220004 runs off the bottom into
    Q-4255-10-DF-099, a DP442 drawing that is not part of this issue.
    """
    pdfs = sorted(DP094.glob("*.pdf"))
    if not pdfs:
        print("\nDP094 set not present - skipping continuation tests")
        return
    print(f"\ncontinuation references ({len(pdfs)} drawings)")
    reports = run_batch(pdfs)

    check(status_in(reports, "220007", "CONT-02") == FAIL,
          "DF-220007 Q-4255-10-DF-22008 is one digit short -> CONT-02 FAIL")
    check("22008" in detail_in(reports, "220007", "CONT-02"),
          "...and the detail names the mistyped reference")
    # Jordan 2026-08-20: a continuation into another package is not a
    # finding. DF-220004 runs off onto DF-099 (DP442) and must stay quiet.
    check(status_in(reports, "220004", "CONT-02") == PASS,
          "DF-220004 continues onto DF-099, another package -> CONT-02 quiet")
    check("other packages" in detail_in(reports, "220004", "CONT-02"),
          "...but the out-of-package count is still reported in the detail")
    check(status_in(reports, "220011", "CONT-02") == PASS,
          "DF-220011's only continuation resolves in the set -> CONT-02 PASS")

    # The standard-detail callouts scattered through the drawing body must not
    # be mistaken for continuation flags -- that is what keeps this check quiet.
    check(all("Q-1300" not in detail_in(reports, r.label, "CONT-02")
              for r in reports),
          "Q-1300-10-DP-0214 body callouts never read as continuations")


def test_dp094_feedback() -> None:
    """Jordan's comments on the 2026-08-10 DP094 issue check (fixed 2026-08-11).

    1. RG630VB16 etc. are drawn as two SHX items ("RG" + "630VB16"); the
       prefixes must be reunited or VLV-01 claims the valves carry no service.
    2. WC-CMN271-3-1 is a water crossing; CMN271 inside it is not a wellsite
       drawn on the sheet, so TB-07 must not flag it against the title.
    3. MAOP-01 findings must anchor on the corridor labels they are about, not
       on the reference-drawings table or the CADFILE/plot stamp.
    """
    from drawing_checker.maop import load as load_maop

    pdfs = sorted(DP094.glob("*.pdf"))
    if not pdfs:
        print("\nDP094 set not present - skipping feedback regression tests")
        return
    print(f"\nDP094 issue-check feedback ({len(pdfs)} drawings)")
    dataset = load_maop(Path(__file__).resolve().parent.parent / "reference")
    reports = run_batch(pdfs, maop=dataset)

    # 1. Split service prefixes reunited.
    check(status_in(reports, "220005", "VLV-01") == PASS,
          "DF-220005 RG/RW prefixes drawn as separate items -> VLV-01 PASS")
    check("no service prefix" not in detail_in(reports, "220005", "VLV-01"),
          "...and VLV-01 no longer claims the valves carry no service")

    # 2. Water crossings are not wellsites.
    check(status_in(reports, "220005", "TB-07") == PASS,
          "DF-220005 WC-CMN271-3-1 crossings not read as wellsites -> TB-07 PASS")
    for label in ("220004", "220006", "220009", "220011"):
        check(status_in(reports, label, "TB-07") in (PASS, NA),
              f"DF-{label} TB-07 quiet (corridor/crossing tags not counted)")

    # 3. MAOP findings anchored on their corridors, in the drawing body.
    if dataset.loaded:
        maop_fails = [
            (r, res) for r in reports for res in r.results
            if res.code == "MAOP-01" and res.status == FAIL
        ]
        check(all(res.anchors for _, res in maop_fails),
              f"every MAOP-01 FAIL carries explicit anchors ({len(maop_fails)} findings)")
        # Sheets are 1191x842; frame furniture lives below y ~733 and in the
        # CADFILE strip left of x ~34.
        check(
            all(y0 < 0.87 * 842 and x0 > 34
                for _, res in maop_fails for (x0, y0, _, _) in res.anchors),
            "no MAOP-01 anchor lands in the frame furniture",
        )


def test_flow_and_tie_ins() -> None:
    """Continuation-flag flow direction and tie-in numbering (2026-08-17).

    The congruence verdict logic is unit-tested with synthetic flags, then the
    real DP094 set locks in the calibration: the only findings are the ones
    with a confirmed cause (the DF-22008 typo breaks one gas pairing, and
    TIP-CMN296-1 legitimately appears on two adjacent sheets).
    """
    from drawing_checker.flow_checks import Flag, _boundary_verdicts

    print("\nflow direction verdict logic (synthetic flags)")

    def flag(direction, service="RG", key=""):
        return Flag(target="Q-4255-10-DF-001", edge="R", direction=direction,
                    service=service, line_key=key, rect=(0, 0, 1, 1))

    v = _boundary_verdicts("DF-002", [flag("out", key="250-RG-1111")],
                          [flag("in", key="250-RG-1111")])
    check([s for s, _, _ in v] == [PASS], "out meets in -> PASS")

    v = _boundary_verdicts("DF-002", [flag("out", key="250-RG-1111")],
                          [flag("out", key="250-RG-1111")])
    check([s for s, _, _ in v] == [FAIL], "head-on out/out -> FAIL")

    v = _boundary_verdicts("DF-002", [flag("in", key="250-RG-1111")],
                          [flag("in", key="250-RG-1111")])
    check([s for s, _, _ in v] == [FAIL], "orphan in/in -> FAIL")

    v = _boundary_verdicts("DF-002", [flag("both", key="250-RG-1111")],
                          [flag("both", key="250-RG-1111")])
    check([s for s, _, _ in v] == [PASS], "both-ended pairs with both-ended -> PASS")

    v = _boundary_verdicts("DF-002", [flag("both", key="250-RG-1111")],
                          [flag("out", key="250-RG-1111")])
    check([s for s, _, _ in v] == [REVIEW],
          "both-ended meeting single-ended -> REVIEW")

    v = _boundary_verdicts("DF-002", [flag("out"), flag("in")],
                          [flag("out"), flag("in")])
    check([s for s, _, _ in v] == [PASS],
          "two unlabelled corridors in opposite directions -> PASS")

    v = _boundary_verdicts("DF-002", [flag("out")], [])
    check([s for s, _, _ in v] == [REVIEW],
          "flag with no counterpart on the other sheet -> REVIEW")

    pdfs = sorted(DP094.glob("*.pdf"))
    if not pdfs:
        print("DP094 set not present - skipping flow/tie-in batch tests")
        return
    print(f"\nflow direction + tie-ins on the DP094 set ({len(pdfs)} drawings)")
    reports = run_batch(pdfs)

    # The only flow finding is the pairing broken by the DF-22008 typo that
    # CONT-02 already fails: DF-220007's gas flag points at the five-digit
    # number, so DF-220008 sees a gas flag arrive with none going back.
    check(status_in(reports, "PID-0043", "FLOW-02") == REVIEW,
          "DF-220007's 22008 typo breaks its gas pairing -> FLOW-02 REVIEW")
    check(status_in(reports, "PID-0044", "FLOW-02") == REVIEW,
          "...and DF-220008 sees the unreciprocated flag -> FLOW-02 REVIEW")
    check(
        all(status_of(r.results, "FLOW-02") in (PASS, NA)
            for r in reports
            if "PID-0043" not in r.label and "PID-0044" not in r.label),
        "FLOW-02 quiet on every intact DP094 boundary",
    )
    check(
        all(status_of(r.results, "FLOW-01") in (PASS, NA) for r in reports),
        "all DP094 pennants show a direction -> FLOW-01 quiet",
    )
    check(
        all(status_of(r.results, "FLOW-03") in (PASS, NA) for r in reports),
        "flow leaves every DP094 sheet -> FLOW-03 quiet",
    )

    # OD560 pipe on the DP094 trunkline sheets (Jordan, 2026-08-20: recommend
    # sizing to OD630 for purchasing).
    check(status_in(reports, "PID-0044", "SIZE-01") == REVIEW,
          "PID-0044 560-RG line -> SIZE-01 REVIEW recommending OD630")
    check(status_in(reports, "PID-0040", "SIZE-01") in (PASS, NA),
          "PID-0040 carries no 560 pipe -> SIZE-01 quiet")

    # The key plan's populated PROJECT NO. box (inverted TB-08).
    check(status_in(reports, "PID-0080", "TB-08") == REVIEW,
          "PID-0080 PROJECT NO. box carries DP094 -> TB-08 REVIEW")

    # A TIP tag belongs to TIP-01 only -- TAG-03 must not double-report it.
    check("TIP-CMN296-1" not in detail_in(reports, "PID-0042", "TAG-03"),
          "TIP-CMN296-1 not double-reported by TAG-03")

    # TIP-CMN296-1 is drawn on both PID-0042 and PID-0050 -- the same physical
    # tie-in on adjacent sheets, which is exactly what REVIEW is for.
    check(status_in(reports, "PID-0042", "TIP-01") == REVIEW,
          "TIP-CMN296-1 on two sheets -> TIP-01 REVIEW on PID-0042")
    check(status_in(reports, "PID-0050", "TIP-01") == REVIEW,
          "TIP-CMN296-1 on two sheets -> TIP-01 REVIEW on PID-0050")
    check("TIP-CMN296-1" in detail_in(reports, "PID-0042", "TIP-01"),
          "...and the detail names the repeated tag")
    check(
        all(status_of(r.results, "TIP-01") in (PASS, NA)
            for r in reports
            if "PID-0042" not in r.label and "PID-0050" not in r.label),
        "TIP-01 quiet on every other DP094 sheet",
    )


ARN12 = Path(__file__).resolve().parent.parent / "reference" / "ARN12"


def test_arn12_dp401() -> None:
    """The ARN12 DP401 set (2026-08-13). Defects confirmed by eye:

    - DF-181P01's bottom continuation label genuinely reads
      250-RW-5164-P128-430 on the drawing (final 0 missing; the same line is
      drawn in full elsewhere on the sheet).
    - Every Rev A revision row carries no date.
    - 95DY462 / 49DY467 / 72DY471 / 1RP188524 are lot-on-plan property
      references beside the property boundaries, NOT mistyped valve tags.
    """
    pdfs = sorted(ARN12.glob("*.pdf"))
    if not pdfs:
        print("\nARN12 DP401 set not present - skipping")
        return
    print(f"\nARN12 DP401 set ({len(pdfs)} drawings)")
    reports = run_batch(pdfs)

    check(all(status_of(r.results, "CMT-03") in (PASS, NA) for r in reports),
          "lot-on-plan references (95DY462 etc.) not read as valve tags")
    check(status_in(reports, "181P01", "LN-01") == FAIL,
          "DF-181P01 truncated 250-RW-5164-P128-430 -> LN-01 FAIL")
    check(all(status_of(r.results, "TB-04") == FAIL for r in reports),
          "undated Rev A revision rows -> TB-04 FAIL on all six")
    check(all(status_of(r.results, "TB-07") in (PASS, NA) for r in reports),
          "TB-07 quiet across the DP401 set")
    check(all(status_of(r.results, "VLV-01") in (PASS, NA) for r in reports),
          "VLV-01 quiet across the DP401 set")

    # Flow direction: verified by eye across the whole set. The gathering runs
    # 220004 -> 177P01 -> 178P01 -> ... with the corridor trunk lines drawn as
    # both-ended pennants that pair with both-ended pennants next door.
    check(all(status_of(r.results, "FLOW-01") == PASS for r in reports),
          "every DP401 continuation flag shows a direction -> FLOW-01 PASS")
    check(all(status_of(r.results, "FLOW-02") == PASS for r in reports),
          "flow directions agree across every DP401 boundary -> FLOW-02 PASS")
    check("both-ended" in detail_in(reports, "PID-0017", "FLOW-02"),
          "...with the 250-RG-5619 trunk matched as both-ended on both sheets")
    check(all(status_of(r.results, "FLOW-03") in (PASS, NA) for r in reports),
          "flow leaves every DP401 sheet -> FLOW-03 quiet")
    check(all(status_of(r.results, "TIP-01") in (PASS, NA) for r in reports),
          "no tie-in number reused across DP401 -> TIP-01 quiet")

    # Jordan 2026-08-13: DF-181P01 showed 2 boxes on the drawing against 5
    # comments on the comment sheet. Sheet-level findings (PLT-01, TB-16) now
    # get a numbered flag in the margin, so every comment number is visible.
    from drawing_checker.markup import mark_up
    import tempfile as _tf

    target = next(r for r in reports if "181P01" in r.label)
    sheet = load_sheets(next(p for p in pdfs if "181P01" in p.name))[0]
    with _tf.TemporaryDirectory() as tmp:
        out, _, total = mark_up(target, sheet, Path(tmp) / "m.pdf")
        doc = fitz.open(out)
        try:
            titles = [
                (a.info or {}).get("title", "")
                for a in doc[0].annots()
                if a.type[1] == "FreeText"
                and (a.info or {}).get("title", "").startswith("Check ")
            ]
        finally:
            doc.close()
        numbers = {t for t in titles if not t.endswith(" summary")}
        check(total > 0 and len(numbers) == total,
              f"every comment number flagged on the drawing ({len(numbers)}/{total})")
        check(not any(t.endswith(" summary") for t in titles),
              "no on-sheet summary labels (removed per Jordan 2026-08-20)")


def main() -> int:
    if not REFERENCE.exists():
        print(f"Reference drawing not found: {REFERENCE}")
        return 1
    with tempfile.TemporaryDirectory() as tmpdir:
        test_reference_extraction()
        test_reference_checks()
        test_mutants(Path(tmpdir))
        test_spelling()
        test_specs()
        test_client_and_note_checks()
        test_maop()
        test_unreadable_set()
        test_ocr_layer()
        test_dxf_extraction()
        test_dxf_checks()
        test_dxf_markup()
        test_dxf_markup_into_dxf()
        test_arn_batch()
        test_continuation_targets()
        test_dp094_feedback()
        test_flow_and_tie_ins()
        test_arn12_dp401()
        test_markup(Path(tmpdir))

    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("All validation checks passed.")
    return 0




DXF_DIR = Path(__file__).resolve().parent.parent / "reference" / "ARN10 PEFs" / "IF439" / "dxfs"
PLOTTED_DIR = DXF_DIR.parent / "pdfs"


def run_dxf_batch(paths, maop=None):
    from drawing_checker.dxf_extract import load_dxf_sheets

    speller = BatchSpeller()
    loaded = []
    for path in paths:
        for sheet in load_dxf_sheets(path):
            tb = parse_title_block(sheet)
            loaded.append((path, sheet, tb))
            speller.add(sheet.label,
                        [dedupe_shx_repeat(x) for x in prose_fragments(sheet, tb)])
    reports, entries = [], []
    for path, sheet, tb in loaded:
        report = SheetReport(
            sheet.label, path.name, tb.drawing_number, tb.revision,
            run_checks(sheet, tb, BatchContext(
                speller=speller, key=sheet.label, maop=maop)))
        reports.append(report)
        entries.append((report, sheet, tb))
    run_batch_checks(entries)
    return reports, entries


def test_dxf_extraction() -> None:
    """A DXF must produce a Sheet the existing checks can read."""
    from drawing_checker.dxf_extract import classify_block, load_dxf_sheets

    print("\nDXF extraction")
    target = DXF_DIR / "Q-4255-10-DF-030_13P03.dxf"
    if not target.exists():
        print("  DXF set not present - skipping")
        return

    sheet = load_dxf_sheets(target)[0]
    check(sheet.from_dxf, "sheet is flagged as DXF-derived")
    check(not sheet.is_ocr, "a DXF is never treated as OCR")
    check((sheet.width, sheet.height) == (1191.0, 842.0),
          f"mapped onto the A3 plot page (got {sheet.width:.0f}x{sheet.height:.0f})")

    tb = parse_title_block(sheet)
    check(tb.drawing_number == "Q-4255-10-DF-030/13P03",
          f"drawing number read from the frame (got {tb.drawing_number!r})")
    check(tb.revision == "A", f"revision read (got {tb.revision!r})")
    check(len(tb.title_lines) == 4, f"4 title lines (got {len(tb.title_lines)})")
    check([n for n, _ in tb.reference_drawings]
          == ["Q-4255-10-DF-026_01", "Q-4255-10-DF-023_07"],
          "reference drawings read from the frame")
    check([r.revision for r in tb.revision_history] == ["A"],
          "revision history read from the frame")

    # The transform is what makes markup land on the plotted PDF.
    number = next((i for i in sheet.items
                   if i.text.strip() == "Q-4255-10-DF-030/13P03"), None)
    check(number is not None and abs(number.x0 - 953) < 12 and abs(number.y0 - 776) < 12,
          "drawing number maps to where it sits on the plotted sheet")

    check(classify_block("PEF_STAGE-4_OVERALL$0$VALVE_UG") == "valve",
          "block names classified through the xref prefix")
    check(classify_block("tag_flowa") == "flow_arrow"
          and classify_block("Flow Arrow") == "flow_arrow",
          "the same symbol is recognised under both naming lineages")


def test_dxf_checks() -> None:
    """The checks that only a DXF can answer."""
    from drawing_checker.maop import load as load_maop

    print("\nDXF-only checks")
    paths = sorted(DXF_DIR.glob("*.dxf"))
    if not paths:
        print("  DXF set not present - skipping")
        return
    reports, _ = run_dxf_batch(paths, load_maop(Path(__file__).resolve().parent.parent / "reference"))
    check(len(reports) == 10, f"all 10 DXFs read (got {len(reports)})")

    # Every sheet has flow arrows and dashed property boundaries.
    check(all(status_of(r.results, "DXF-02") == PASS for r in reports),
          "flow direction shown on every sheet -> DXF-02 PASS")
    boundaries = [r for r in reports if status_of(r.results, "DXF-03") != NA]
    check(boundaries and all(status_of(r.results, "DXF-03") == PASS for r in boundaries),
          f"property boundaries dashed on all {len(boundaries)} sheets that have them")

    # The stale continuation reference: 1157 points at 1158P02, set holds P03.
    check(status_in(reports, "1157P02", "CONT-01") == FAIL,
          "1157P02 points at a superseded revision of 1158 -> CONT-01 FAIL")
    check(status_in(reports, "1154P01", "CONT-01") == PASS,
          "1154P01 continuation references all resolve -> CONT-01 PASS")

    # Checks about the issued PDF must not fire on a DXF.
    for code in ("FN-01", "TB-10", "PLT-02"):
        check(all(status_of(r.results, code) == NA for r in reports),
              f"{code} is N/A on a DXF (it is about the issued PDF)")

    # DXF-06 is informational until connectivity is accurate enough.
    check(all(status_of(r.results, "DXF-06") == PASS for r in reports),
          "DXF-06 never fails - connectivity is informational only")


def test_dxf_markup() -> None:
    """DXF findings are drawn on the plotted PDF, not on the DXF."""
    from drawing_checker.markup import mark_up
    import tempfile as _tempfile

    print("\nDXF markup onto the plotted PDF")
    target = DXF_DIR / "Q-4255-10-DF-030_13P03.dxf"
    plotted = PLOTTED_DIR / "Q-4255-10-DF-030_13P03.pdf"
    if not target.exists() or not plotted.exists():
        print("  DXF set or plotted PDFs not present - skipping")
        return

    reports, entries = run_dxf_batch([target])
    report, sheet, _ = entries[0]
    with _tempfile.TemporaryDirectory() as tmp:
        out, placed, total = mark_up(report, sheet, Path(tmp) / "m.pdf", plotted)
        check(out.exists(), "marked-up PDF written from the plotted sheet")
        check(placed > 0, f"findings pinned to the drawing ({placed}/{total})")
        doc = fitz.open(out)
        try:
            page = doc[0]
            original = fitz.open(plotted)
            try:
                check(page.read_contents() == original[0].read_contents(),
                      "the plotted drawing's content stream is untouched")
            finally:
                original.close()
            # Read the values out while the annotations are still owned by a
            # live page -- holding Annot objects past that segfaults PyMuPDF.
            width = page.rect.width
            boxes = [
                (a.rect.x0, a.rect.x1)
                for a in page.annots()
                if a.type[1] == "Square"
                and (a.info or {}).get("title", "").startswith("Check ")
            ]
            check(bool(boxes), "callout boxes added over the drawing")
            # 1pt of slack: annotation rects are rounded when the PDF is
            # written, so a box clamped exactly to the edge reads back as
            # a fraction over.
            check(all(-1 <= x0 and x1 <= width + 1 for x0, x1 in boxes),
                  "every callout lands inside the page")
        finally:
            doc.close()


def test_dxf_markup_into_dxf() -> None:
    """Findings written back into a copy of the DXF, on their own layers."""
    import ezdxf
    import tempfile as _tempfile
    from drawing_checker.dxf_markup import LAYERS, SCHEDULE_LAYOUT, mark_up_dxf

    print("\nmarkup written into the DXF")
    target = DXF_DIR / "Q-4255-10-DF-030_13P03.dxf"
    if not target.exists():
        print("  DXF set not present - skipping")
        return

    _, entries = run_dxf_batch([target])
    report, sheet, _ = entries[0]
    with _tempfile.TemporaryDirectory() as tmp:
        out, drawn, total = mark_up_dxf(report, sheet, Path(tmp) / "m.dxf")
        check(out.exists(), "marked-up DXF written")
        check(drawn > 0, f"findings drawn on the sheet ({drawn}/{total})")

        doc = ezdxf.readfile(out)
        names = {n for n, _ in LAYERS.values()}
        check(all(n in doc.layers for n in names), "CHECK-* layers created")
        layout = doc.layout(sheet.dxf.layout)
        markup = [e for e in layout if e.dxf.layer in names]
        check(bool(markup), "callouts added to the plotted layout")
        check(all(e.dxf.layer in names for e in markup),
              "all markup sits on CHECK-* layers, so it can be purged")
        check(SCHEDULE_LAYOUT in doc.layout_names(),
              "a comment schedule layout is added")

        # The source drawing must be untouched: same entity count outside the
        # markup layers, and the original file unchanged on disk.
        source = ezdxf.readfile(target)
        before = sum(1 for e in source.layout(sheet.dxf.layout))
        after = sum(1 for e in layout if e.dxf.layer not in names)
        check(before == after,
              f"drawing content unchanged ({before} entities before, {after} after)")

        # Callouts must land on the sheet, not off it.
        boxes = [e for e in markup if e.dxftype() == "LWPOLYLINE"]
        points = [p for b in boxes for p in b.get_points("xy")]
        check(bool(points), "callout boxes are real geometry")
        check(all(-50 < x < 850 and -50 < y < 620 for x, y in points),
              "every callout lands within the sheet's paper space")


if __name__ == "__main__":
    raise SystemExit(main())
