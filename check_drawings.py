"""Check a folder of PEFS drawing PDFs and write an Excel report.

    python check_drawings.py <folder-or-pdf> [more...] [-o report.xlsx] [--recursive]

Drop the final PDFs in a folder, point this at it, and open the report.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from drawing_checker.checks import FAIL, NO_DATA, REVIEW, run_checks
from drawing_checker.content_checks import BatchContext, run_batch_checks
from drawing_checker.dxf_extract import load_dxf_sheets
from drawing_checker.extract import dedupe_shx_repeat, load_sheets
from drawing_checker.maop import load as load_maop
from drawing_checker.dxf_markup import mark_up_dxf
from drawing_checker.markup import mark_up
from drawing_checker.report import SheetReport, write_report
from drawing_checker.spelling import BatchSpeller, prose_fragments
from drawing_checker.specs import spec_source_warnings
from drawing_checker.titleblock import parse as parse_title_block


SUFFIXES = (".pdf", ".dxf")


def collect_inputs(targets: list[str], recursive: bool) -> list[Path]:
    """PDFs and DXFs, in the order given, de-duplicated."""
    found: list[Path] = []
    for target in targets:
        path = Path(target)
        if path.is_dir():
            for suffix in SUFFIXES:
                pattern = f"*{suffix}"
                found.extend(sorted(
                    path.rglob(pattern) if recursive else path.glob(pattern)
                ))
        elif path.suffix.lower() in SUFFIXES:
            found.append(path)
        else:
            print(f"  skipped (not a PDF, DXF or folder): {path}", file=sys.stderr)
    return list(dict.fromkeys(found))


def load_any(path: Path):
    """Sheets from either a PDF or a DXF."""
    if path.suffix.lower() == ".dxf":
        return load_dxf_sheets(path)
    return load_sheets(path)


def markup_target(source: Path) -> Path | None:
    """The plotted PDF a DXF's findings should be drawn on.

    Looks for a PDF of the same name beside the DXF, then in a sibling
    ``pdfs`` folder -- which is where the batch exporter puts them.
    """
    if source.suffix.lower() == ".pdf":
        return source
    for candidate in (
        source.with_suffix(".pdf"),
        source.parent.parent / "pdfs" / f"{source.stem}.pdf",
        source.parent / "pdfs" / f"{source.stem}.pdf",
    ):
        if candidate.exists():
            return candidate
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("targets", nargs="+", help="PDF files and/or folders of PDFs")
    ap.add_argument("-o", "--output", help="output .xlsx path")
    ap.add_argument("-r", "--recursive", action="store_true",
                    help="search sub-folders too")
    ap.add_argument("-m", "--markup", nargs="?", const="marked-up", default=None,
                    metavar="DIR",
                    help="also write marked-up PDFs (default folder: marked-up)")
    args = ap.parse_args(argv)

    inputs = collect_inputs(args.targets, args.recursive)
    if not inputs:
        print("No PDFs or DXFs found.", file=sys.stderr)
        return 1

    reference = Path(__file__).resolve().parent / "reference"
    maop = load_maop(reference)
    if maop.loaded:
        print(f"MAOP review: {maop.path.name} ({len(maop.by_corridor)} corridors)\n")
    else:
        print("MAOP review: none found in reference/ - MAOP-01 will report NO DATA\n")

    for warning in spec_source_warnings(reference):
        print(f"  !! spec out of date: {warning}", file=sys.stderr)

    # Pass 1: read every sheet, and build the batch-wide spelling model. The
    # speller needs all the prose before it can tell a typo from house
    # vocabulary, so no checks can run until every sheet has been read.
    loaded: list[tuple[Path, object, object]] = []
    speller = BatchSpeller()
    for pdf in inputs:
        try:
            sheets = load_any(pdf)
        except Exception as exc:  # a corrupt PDF must not stop the batch
            print(f"  !! {pdf.name}: could not be opened ({exc})", file=sys.stderr)
            continue
        for sheet in sheets:
            tb = parse_title_block(sheet)
            loaded.append((pdf, sheet, tb))
            speller.add(
                sheet.label,
                [dedupe_shx_repeat(f) for f in prose_fragments(sheet, tb)],
            )

    # Pass 2: run the checks.
    reports: list[SheetReport] = []
    entries = []
    for pdf, sheet, tb in loaded:
        report = SheetReport(
            label=sheet.label,
            filename=pdf.name,
            drawing_number=tb.drawing_number,
            revision=tb.revision,
            results=run_checks(
                sheet, tb,
                BatchContext(speller=speller, key=sheet.label, maop=maop),
            ),
        )
        reports.append(report)
        entries.append((report, sheet, tb))

    # Pass 3: cross-drawing checks, appended to the reports in place.
    run_batch_checks(entries)

    for report in reports:
        if report.skipped:
            continue
        flag = (
            "FAIL" if report.count(FAIL)
            else "review" if report.count(REVIEW)
            else "ok"
        )
        print(
            f"  {report.label[:56]:58s} {report.drawing_number or '-':22s} "
            f"rev {report.revision or '-':3s} "
            f"{report.count(FAIL)} fail / {report.count(REVIEW)} review / "
            f"{report.count(NO_DATA)} no-data   [{flag}]"
        )

    out = Path(args.output) if args.output else Path(
        f"PEFS check report {datetime.now():%Y-%m-%d %H%M}.xlsx"
    )
    write_report(reports, out)

    if args.markup is not None:
        markup_dir = Path(args.markup)
        print(f"\nMarking up PDFs into {markup_dir.resolve()}")
        for report, sheet, _tb in entries:
            if report.skipped:
                continue
            origin = Path(sheet.pdf_path)
            if origin.suffix.lower() == ".dxf":
                dxf_out = markup_dir / f"{origin.stem} MARKED UP.dxf"
                try:
                    _, drawn, total = mark_up_dxf(report, sheet, dxf_out)
                    print(f"  {dxf_out.name[:62]:64s} {drawn}/{total} finding(s) "
                          "drawn on the DXF")
                except Exception as exc:
                    print(f"  !! {report.label}: DXF markup failed ({exc})",
                          file=sys.stderr)
            source = markup_target(origin)
            if source is None:
                print(
                    f"  -- {Path(report.filename).stem[:56]:58s} no plotted PDF "
                    "found to mark up (looked beside the DXF and in ../pdfs)"
                )
                continue
            target = markup_dir / f"{Path(report.filename).stem} MARKED UP.pdf"
            try:
                _, placed, total = mark_up(report, sheet, target, source)
            except Exception as exc:
                print(f"  !! {report.label}: markup failed ({exc})", file=sys.stderr)
                continue
            margin = total - placed
            print(
                f"  {target.name[:62]:64s} {placed}/{total} finding(s) "
                "pinned to the drawing"
                + (f" (+{margin} sheet-level, flagged in the margin)"
                   if margin else "")
            )

    skipped = sum(1 for r in reports if r.skipped)
    if skipped:
        print(f"\n  ({skipped} page(s) skipped - not Origin drawing sheets)")

    # A whole set plotted without the SHX capture is the one failure that
    # invalidates everything else, so say it once, plainly, at the end.
    unreadable = [
        r for r in reports if not r.skipped
        and any(res.code == "EXT-01" and res.status == FAIL for res in r.results)
    ]
    checkable = [r for r in reports if not r.skipped and r not in unreadable]
    if unreadable:
        print(
            f"\n  *** {len(unreadable)} of {len(unreadable) + len(checkable)} "
            "drawing(s) carry NO readable text and could not be checked at all."
        )
        print(
            "      They were plotted without 'capture SHX fonts as comments'.\n"
            "      In AutoCAD set PDFSHX = 1 (or tick 'Capture fonts as "
            "comments' in the\n      PDF export options) and re-plot. Nothing "
            "below applies to them."
        )

    print(f"\nReport: {out.resolve()}")

    return 2 if any(r.count(FAIL) for r in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
