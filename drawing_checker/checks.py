"""Slice 1 checks: drawing frame and identity.

Scope is deliberately narrow -- every check here is objectively decidable from
the text layer, so a FAIL is always a real defect.  Anything that needs
judgement is raised as REVIEW instead, and anything the tool cannot see is
NO DATA rather than a silent pass.

Each check carries the row it came from in
``ARN10-G-LIST-0135 Rev A - PEFs Drawing Checklist``.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .client_checks import run_client_checks
from .content_checks import BatchContext, run_batch_checks, run_content_checks
from .dxf_checks import run_dxf_checks
from .extract import Sheet
from .geometry_checks import run_geometry_checks
from .maop import run_maop_check
from .notes import run_note_checks
from .specs import run_spec_checks
from .titleblock import (
    ORIGIN_DWG_NO, REVISION_VALUE, TitleBlock, normalise_drawing_number,
)

PASS = "PASS"
FAIL = "FAIL"
REVIEW = "REVIEW"
NA = "N/A"
NO_DATA = "NO DATA"
SKIPPED = "SKIPPED"

# Wellsite tags as drawn on Origin gathering PEFS, e.g. ORN359.
WELL_TAG = re.compile(r"\b([A-Z]{3}\d{3})\b")
# A wellsite is *drawn* on a sheet when its tag stands alone ("CMN270" beside
# the wellhead). A tag inside a corridor label ("CMN271-3-1") or a crossing /
# tie-in tag ("WC-CMN271-3-1", "TIP-CMN298-4") references a corridor passing
# through the sheet, not a wellsite drawn on it -- counting those made TB-07
# mistake water crossings for wellsites.
BARE_WELL_TAG = re.compile(r"(?<![A-Z0-9-])([A-Z]{3}\d{3})(?![\d-])")
# Origin naming: <dwg no>_<rev>_<status>.pdf
REV_TOKEN = r"P\d{1,2}|\d{1,2}[A-Z]?|[A-Z]"
ORIGIN_FILENAME = re.compile(
    r"^(?P<no>[A-Z]-\d{4}-\d{2}-[A-Z]{2}-\d{2,6}(?:P\d{2})?)"
    rf"_(?P<rev>{REV_TOKEN})_(?P<status>[A-Z]{{3}})$"
)
# Promech naming: ARN05-F-PID-0019 Rev A - <title> - <origin dwg no>
PROMECH_FILENAME = re.compile(
    r"^(?P<promech>ARN\d{2}-[A-Z]{1,3}-[A-Z]{2,5}-\d{4})\s+"
    rf"Rev\s+(?P<rev>{REV_TOKEN})\s*-\s*(?P<rest>.+)$",
    re.IGNORECASE,
)
DATE_ANY = re.compile(r"\b\d{1,2}([./])\d{2}\1\d{4}\b")


@dataclass
class Result:
    code: str
    group: str
    check: str
    status: str
    detail: str = ""
    checklist_ref: str = ""
    # Where on the sheet the finding is, in displayed page coordinates, so the
    # markup pass can put the comment next to the thing it is about. Empty
    # means the finding is about the sheet as a whole.
    anchors: list[tuple[float, float, float, float]] = field(default_factory=list)


@dataclass
class FileName:
    convention: str = ""  # "origin" | "promech" | ""
    drawing_number: str = ""
    revision: str = ""
    status_code: str = ""
    promech_number: str = ""
    title_part: str = ""


def parse_filename(path: Path) -> FileName:
    stem = path.stem.strip()
    if m := ORIGIN_FILENAME.match(stem):
        return FileName(
            convention="origin",
            drawing_number=m["no"],
            revision=m["rev"],
            status_code=m["status"],
        )
    if m := PROMECH_FILENAME.match(stem):
        rest = m["rest"]
        numbers = ORIGIN_DWG_NO.findall(rest.upper())
        return FileName(
            convention="promech",
            drawing_number=numbers[-1] if numbers else "",
            revision=m["rev"].upper(),
            promech_number=m["promech"].upper(),
            title_part=rest,
        )
    return FileName()


def _sheet_number(drawing_number: str) -> int | None:
    """Trailing sheet number of an Origin drawing number, P-suffix removed."""
    m = re.search(r"-(\d{2,6})(?:/(\d{1,2}))?(?:P\d{2})?$", drawing_number.upper())
    return int(m[1]) if m else None


def _is_prelim(revision: str) -> bool:
    return revision.upper().startswith("P")


def _is_alpha(revision: str) -> bool:
    return len(revision) == 1 and revision.isalpha()


def run_checks(
    sheet: Sheet, tb: TitleBlock, context: BatchContext | None = None
) -> list[Result]:
    fn = parse_filename(sheet.pdf_path)
    out: list[Result] = []

    # Checks about the issued artefact itself. A DXF is the source, not the
    # deliverable, so these say nothing about it -- keep running them on the
    # plotted PDF instead.
    PDF_ONLY = {
        "FN-01", "FN-02", "FN-03", "FN-04", "FN-05",
        "TB-10", "TB-11", "TB-17", "PLT-01", "PLT-02",
    }

    def add(code, group, check, status, detail="", ref="", anchors=None):
        if sheet.from_dxf and code in PDF_ONLY:
            status, detail = NA, (
                "Not applicable to a DXF - this check is about the issued PDF "
                "(filename, CADFILE stamp, plot metadata). Run the tool on the "
                "plotted PDF for it."
            )
        # An OCR text layer misreads characters, and every precise check here
        # is a character-level comparison. Nothing read that way is allowed to
        # assert a defect outright -- the worst it can say is "look at this".
        if sheet.is_ocr and status == FAIL:
            status = REVIEW
            detail = (
                f"{detail} [Read from an OCR text layer, so this may be a "
                "misread rather than a drawing error - confirm against the "
                "drawing.]"
            )
        out.append(
            Result(code, group, check, status, detail, ref, list(anchors or []))
        )

    # -- gate -----------------------------------------------------------
    if not sheet.has_text_layer:
        if sheet.looks_like_drawing:
            cause = (
                "The page is a raster scan (a full-page image), so it carries "
                "no text at all. It cannot be checked automatically - it needs "
                "a manual check, or the native drawing re-plotted."
                if sheet.is_scan
                else "The PDF was plotted without 'capture SHX fonts as "
                "comments' (AutoCAD's PDF options), so the drawing text is "
                "stroked geometry only. Re-plot with that option on."
            )
            add(
                "EXT-01", "Extraction", "PDF carries a readable text layer", FAIL,
                f"This is a drawing sheet ({sheet.width:.0f}x{sheet.height:.0f}pt, "
                f"{sheet.vector_count} vector paths) but only {len(sheet.items)} "
                f"text items could be recovered, so NONE of the checks below "
                f"could run. {cause}",
                "n/a - tool prerequisite",
            )
        else:
            add(
                "EXT-01", "Extraction", "PDF carries a readable text layer", SKIPPED,
                "Not a plotted drawing sheet - no Origin frame and little "
                "vector content. Ignored.",
                "n/a - tool prerequisite",
            )
        return out
    add(
        "EXT-01", "Extraction", "PDF carries a readable text layer", PASS,
        f"{len(sheet.items)} text items recovered "
        f"({sum(1 for i in sheet.items if i.source == 'shx')} from SHX capture).",
        "n/a - tool prerequisite",
    )
    if sheet.is_ocr:
        add(
            "EXT-02", "Extraction", "Text layer is authoritative, not OCR", REVIEW,
            "This sheet's text came from an OCR pass, not from AutoCAD's SHX "
            "capture. OCR misreads characters - measured on this set, about 3% "
            "of line numbers and 8% of corridor tags came back malformed "
            "(RG read as R0/R6/2G, 0 read as O). Every check below is therefore "
            "capped at REVIEW and none of it should be treated as proof of a "
            "defect. Re-plot with PDFSHX = 1 for a text layer that can be "
            "trusted.",
            "n/a - tool prerequisite",
        )
    else:
        add(
            "EXT-02", "Extraction", "Text layer is authoritative, not OCR", PASS,
            "Text came from AutoCAD's SHX capture - exact strings, no "
            "recognition errors.", "n/a - tool prerequisite",
        )

    if not tb.found:
        drawingish = sheet.looks_like_drawing
        add(
            "TB-00", "Title Block", "Origin drawing frame recognised",
            REVIEW if drawingish else SKIPPED,
            "The Origin frame labels ('DRAWING NO.', 'TITLE') were not found. "
            + (
                "The page does look like a plotted sheet, so either it uses a "
                "different title block (e.g. a GIS map export) or the frame is "
                "non-standard - check this one by hand."
                if drawingish
                else "Not a drawing sheet. Ignored."
            ),
            "Title Block",
        )
        return out

    # -- filename -------------------------------------------------------
    if fn.convention == "promech":
        add("FN-01", "Drawing File", "Filename follows a known convention", PASS,
            f"Promech convention: {fn.promech_number} Rev {fn.revision}.", "Row 81")
    elif fn.convention == "origin":
        add("FN-01", "Drawing File", "Filename follows a known convention", PASS,
            f"Origin convention: {fn.drawing_number} rev {fn.revision} "
            f"({fn.status_code}).", "Row 81")
    else:
        add("FN-01", "Drawing File", "Filename follows a known convention", FAIL,
            f"'{sheet.pdf_path.stem}' matches neither the Promech convention "
            "(ARNxx-X-XXX-0000 Rev n - Title - Origin no) nor the Origin "
            "convention (<dwg no>_<rev>_<status>).", "Row 81")

    if fn.drawing_number:
        same = (normalise_drawing_number(fn.drawing_number)
                == normalise_drawing_number(tb.drawing_number))
        add("FN-02", "Drawing File", "Filename drawing number matches title block",
            PASS if same else FAIL,
            f"filename {fn.drawing_number} vs title block {tb.drawing_number}.",
            "Rows 14, 81")
    else:
        add("FN-02", "Drawing File", "Filename drawing number matches title block",
            NO_DATA, "No drawing number could be read from the filename.",
            "Rows 14, 81")

    if fn.revision:
        same = fn.revision.upper() == tb.revision.upper()
        add("FN-03", "Drawing File", "Filename revision matches title block",
            PASS if same else FAIL,
            f"filename rev {fn.revision} vs title block rev {tb.revision}.",
            "Rows 14, 81")
    else:
        add("FN-03", "Drawing File", "Filename revision matches title block",
            NO_DATA, "No revision could be read from the filename.", "Rows 14, 81")

    if fn.convention == "promech":
        title_ok = bool(fn.title_part) and len(fn.title_part) > len(fn.drawing_number) + 5
        add("FN-04", "Drawing File", "Filename includes the drawing title",
            PASS if title_ok else REVIEW,
            f"title portion: '{fn.title_part}'.", "Row 81")
        add("FN-05", "Drawing File", "Filename wellsites agree with the title block",
            *_filename_wellsite_verdict(fn, tb), "Rows 15, 40, 81")
    else:
        add("FN-04", "Drawing File", "Filename includes the drawing title", NA,
            "Origin-convention filenames do not carry the title.", "Row 81")
        add("FN-05", "Drawing File", "Filename wellsites agree with the title block",
            NA, "Origin-convention filenames do not list wellsites.",
            "Rows 15, 40, 81")

    # -- drawing number / revision --------------------------------------
    if not tb.drawing_number:
        add("TB-01", "Title Block", "Drawing number present and well formed",
            NO_DATA, "Nothing read from the DRAWING NO. box.", "Rows 11, 38")
    elif ORIGIN_DWG_NO.fullmatch(tb.drawing_number):
        add("TB-01", "Title Block", "Drawing number present and well formed", PASS,
            tb.drawing_number, "Rows 11, 38")
    else:
        add("TB-01", "Title Block", "Drawing number present and well formed", FAIL,
            f"'{tb.drawing_number}' is not a valid Origin drawing number.",
            "Rows 11, 38")

    if not tb.revision:
        add("TB-02", "Title Block", "Revision present and valid", FAIL,
            "The REVISION box is empty.", "Rows 13, 42")
    elif REVISION_VALUE.match(tb.revision):
        add("TB-02", "Title Block", "Revision present and valid", PASS,
            f"Rev {tb.revision}.", "Rows 13, 42")
    else:
        add("TB-02", "Title Block", "Revision present and valid", FAIL,
            f"'{tb.revision}' is not a valid revision.", "Rows 13, 42")

    # -- revision history ------------------------------------------------
    if not tb.revision_history:
        add("TB-03", "Title Block", "Revision matches latest revision history row",
            NO_DATA, "No revision history rows could be read.", "Row 46")
        add("TB-04", "Title Block", "Latest revision row is complete", NO_DATA,
            "No revision history rows could be read.", "Row 46")
    else:
        latest = tb.revision_history[0]
        # Pin revision-history findings on the row itself. The description is
        # the only cell distinctive enough to find (a bare "A" matches all
        # over the sheet).
        row_anchors = [
            (i.x0, i.y0, i.x1, i.y1)
            for i in sheet.items
            if i.cy > 0.9 * sheet.height
            and latest.description.strip()
            and i.text.strip().upper() == latest.description.strip().upper()
        ][:2]
        same = latest.revision.upper() == tb.revision.upper()
        add("TB-03", "Title Block", "Revision matches latest revision history row",
            PASS if same else FAIL,
            f"title block rev {tb.revision} vs top history row rev "
            f"{latest.revision} ({latest.description or 'no description'}).",
            "Row 46", anchors=None if same else row_anchors)

        missing = [
            name
            for name, value in [("date", latest.date), ("description", latest.description)]
            if not value.strip()
        ]
        add("TB-04", "Title Block", "Latest revision row is complete",
            PASS if not missing else FAIL,
            f"Rev {latest.revision}: {latest.date or '(no date)'} - "
            f"{latest.description or '(no description)'}"
            + (f"; missing {', '.join(missing)}." if missing else "."),
            "Row 46", anchors=row_anchors if missing else None)

    # -- title ------------------------------------------------------------
    if len(tb.title_lines) >= 3:
        add("TB-05", "Title Block", "Drawing title populated", PASS,
            " / ".join(tb.title_lines), "Rows 15, 39")
    elif tb.title_lines:
        add("TB-05", "Title Block", "Drawing title populated", REVIEW,
            f"Only {len(tb.title_lines)} title line(s) read: {tb.title}. "
            "The Origin frame normally carries 4.", "Rows 15, 39")
    else:
        add("TB-05", "Title Block", "Drawing title populated", FAIL,
            "The TITLE box is empty.", "Rows 15, 39")

    upper_title = tb.title.upper()
    if ("PROCESS ENGINEERING FLOW SCHEMATIC" in upper_title
            or re.search(r"\bPEFS?\b", upper_title)):
        add("TB-06", "Title Block", "Title identifies the drawing as a PEFS", PASS,
            "", "Row 39")
    else:
        add("TB-06", "Title Block", "Title identifies the drawing as a PEFS", REVIEW,
            f"Neither 'PROCESS ENGINEERING FLOW SCHEMATIC' nor 'PEFS' "
            f"appears in the title: {tb.title}", "Row 39")

    # -- wellsites named in the title -------------------------------------
    body = [
        i for i in sheet.items
        if i.cy < 0.85 * sheet.height or i.cx < 0.66 * sheet.width
    ]
    body_blob = " ".join(i.text for i in body)
    # A drawn wellsite also appears in corridor form somewhere on the sheet
    # (ORN359 beside the wellhead, ORN359-1 on its flowlines). Requiring that
    # keeps instrument tags, which match the same three-letters-three-digits
    # shape (PIC006), from being read as wellsites.
    corridor_wells = set(re.findall(r"\b([A-Z]{3}\d{3})-\d", body_blob))
    mentions = Counter(
        t for t in BARE_WELL_TAG.findall(body_blob) if t in corridor_wells
    )
    wells = sorted(mentions)
    if not wells:
        add("TB-07", "Title Block", "Title names every wellsite drawn", NA,
            "No standalone wellsite tags on the sheet (corridor, crossing and "
            "tie-in tags reference corridors, not drawn wellsites).",
            "Rows 15, 40")
    elif "WELLSITE" not in upper_title:
        add("TB-07", "Title Block", "Title names every wellsite drawn", NA,
            f"Title is not a wellsite drawing (key plan / property based). "
            f"Wellsites drawn: {', '.join(wells)}.", "Rows 15, 40")
    else:
        # A wellsite PEFS legitimately shows neighbouring wellsites for
        # continuations and tie-ins, so "within reason" (checklist row 15)
        # means only flagging one drawn about as heavily as the subject.
        busiest = max(mentions.values())
        missing = [w for w in wells if w not in upper_title]
        prominent = [w for w in missing if mentions[w] >= 0.5 * busiest]
        detail = (
            f"title names {', '.join(sorted(set(WELL_TAG.findall(upper_title)))) or 'none'}"
            + (f"; also drawn: {', '.join(f'{w} ({mentions[w]}x)' for w in missing)}"
               if missing else "; no others drawn")
        )
        add("TB-07", "Title Block", "Title names every wellsite drawn",
            REVIEW if prominent else PASS,
            detail + (
                f". {', '.join(prominent)} appear(s) about as often as the "
                "subject wellsite - confirm the title should not name it."
                if prominent else "."
            ),
            "Rows 15, 40")

    # -- project number ----------------------------------------------------
    # Jordan (2026-08-20): the PROJECT NO. box should be EMPTY on Origin
    # gathering jobs -- a populated box is the finding, not an empty one.
    if tb.project_no:
        # Anchor on the value inside the PROJECT NO. box itself. Without an
        # explicit anchor the markup pass located the quoted value ('DP401')
        # and pinned the callout on the drawing body's project-number tags,
        # which carry the same text (Jordan's combined.pdf comment).
        label = next(
            (i for i in sheet.items if i.text.strip() == "PROJECT NO."), None)
        value = None
        if label is not None:
            near = [
                i for i in sheet.items
                if i.text.strip() == tb.project_no
                and abs(i.cx - label.cx) < 60 and abs(i.cy - label.cy) < 25
            ]
            value = min(
                near,
                key=lambda i: abs(i.cx - label.cx) + abs(i.cy - label.cy),
                default=None,
            )
        anchor = value or label
        add("TB-08", "Title Block", "Project number box left empty", REVIEW,
            f"PROJECT NO. box carries '{tb.project_no}' - it should be blank "
            "on Origin gathering jobs.", "Rows 17, 43",
            anchors=[(anchor.x0, anchor.y0, anchor.x1, anchor.y1)]
            if anchor is not None else None)
        if tb.project_no.upper() in upper_title:
            add("TB-09", "Title Block", "Project number agrees with the title", PASS,
                "", "Rows 17, 43")
        else:
            add("TB-09", "Title Block", "Project number agrees with the title", REVIEW,
                f"Project box says {tb.project_no} but the title does not "
                f"mention it: {tb.title}", "Rows 17, 43")
    else:
        add("TB-08", "Title Block", "Project number box left empty", PASS,
            "PROJECT NO. box is empty, as it should be on Origin gathering "
            "jobs.", "Rows 17, 43")
        add("TB-09", "Title Block", "Project number agrees with the title", NA,
            "No project number to compare.", "Rows 17, 43")

    # -- CADFILE ------------------------------------------------------------
    if not tb.cadfile_border:
        add("TB-10", "Title Block", "CADFILE printed under the drawing border", FAIL,
            "No .DWG path found beneath the border.", "Rows 16, 41")
        add("TB-11", "Title Block", "CADFILE names this drawing", NO_DATA,
            "No CADFILE to compare.", "Rows 16, 41")
    else:
        dwg = re.search(r"([^\\/]+\.DWG)", tb.cadfile_border.upper())
        stem = dwg[1][:-4] if dwg else ""
        add("TB-10", "Title Block", "CADFILE printed under the drawing border", PASS,
            stem or tb.cadfile_border[:80], "Rows 16, 41")
        add("TB-11", "Title Block", "CADFILE names this drawing",
            *_cadfile_verdict(stem, tb.drawing_number), "Rows 16, 41")

    # -- sign-off boxes ------------------------------------------------------
    add("TB-12", "Title Block", "Sign-off boxes correct for this revision type",
        *_signoff_verdict(tb), "Rows 18, 44")

    # -- reference drawings ---------------------------------------------------
    if tb.reference_drawings:
        add("TB-13", "Title Block", "Reference drawings table populated", PASS,
            "; ".join(f"{n} {t}".strip() for n, t in tb.reference_drawings),
            "Rows 19, 47")
        bad = [n for n, _ in tb.reference_drawings if not ORIGIN_DWG_NO.fullmatch(n)]
        untitled = [n for n, t in tb.reference_drawings if not t.strip()]
        problems = []
        if bad:
            problems.append(f"malformed number(s): {', '.join(bad)}")
        if untitled:
            problems.append(f"no title against: {', '.join(untitled)}")
        add("TB-14", "Title Block", "Reference drawing entries well formed",
            PASS if not problems else FAIL, "; ".join(problems), "Rows 19, 47")
    else:
        add("TB-13", "Title Block", "Reference drawings table populated", REVIEW,
            "The reference drawings table is empty - a PEFS normally cites at "
            "least the standard symbology sheet (A-1000-10-DH-009) and its key "
            "plan.", "Rows 19, 47")
        add("TB-14", "Title Block", "Reference drawing entries well formed", NA,
            "No reference drawings listed.", "Rows 19, 47")

    # -- derived-from note ------------------------------------------------------
    if _is_prelim(tb.revision):
        add("TB-15", "Title Block", "'Drawing derived from' note present (P revs)",
            PASS if tb.derived_from else FAIL,
            tb.derived_from[:160] or "No 'DERIVED FROM' statement found and this "
            "is a P revision.", "Rows 22, 48")
    else:
        add("TB-15", "Title Block", "'Drawing derived from' note present (P revs)",
            NA, f"Rev {tb.revision} is not a P revision.", "Rows 22, 48")

    # -- date format consistency --------------------------------------------------
    seps = {
        m[1]
        for value in (
            [s.date for s in tb.signoffs] + [r.date for r in tb.revision_history]
        )
        if value and (m := DATE_ANY.search(value))
    }
    if len(seps) > 1:
        add("TB-16", "Title Block", "Frame dates use one consistent format", REVIEW,
            "Both dot and slash date formats appear in the frame: "
            + "; ".join(
                f"{s.role}={s.date}" for s in tb.signoffs if s.date
            )
            + "; "
            + "; ".join(f"rev {r.revision}={r.date}" for r in tb.revision_history if r.date),
            "Rows 18, 46")
    else:
        add("TB-16", "Title Block", "Frame dates use one consistent format",
            PASS if seps else NO_DATA,
            "" if seps else "No dates in the sign-off boxes or revision rows "
            "to compare (see TB-04/TB-12 for whether they should be there).",
            "Rows 18, 46")

    # -- PDF internals -------------------------------------------------------------
    add("TB-17", "Drawing File", "PDF layout name is consistent with the drawing",
        *_layout_name_verdict(sheet, tb), "Row 14")

    ctx = context or BatchContext()
    run_content_checks(sheet, tb, ctx, add)
    run_spec_checks(sheet, tb, add)
    run_geometry_checks(sheet, tb, add)
    run_note_checks(sheet, tb, add)
    run_client_checks(sheet, tb, add)
    if sheet.from_dxf:
        run_dxf_checks(sheet, tb, add)
    if ctx.maop is not None:
        run_maop_check(sheet, tb, ctx.maop, add)
    return out


def _layout_name_verdict(sheet, tb: TitleBlock) -> tuple[str, str]:
    """The PDF's internal title is the AutoCAD layout tab it was plotted from.

    Two conventions are in use and both are fine: naming the tab after the
    drawing, or after the revision. What matters is catching a tab that names
    a *different* drawing, which means the sheet was copied and not renamed.
    """
    name = (sheet.meta.get("title") or "").strip()
    if not name:
        return NO_DATA, "The PDF carries no internal layout name."

    if ORIGIN_DWG_NO.search(name.upper()):
        base = re.sub(r"P\d{2}$", "", tb.drawing_number.upper())
        if base and base in name.upper():
            return PASS, f"layout '{name}' names this drawing."
        return REVIEW, (
            f"The layout tab is named '{name}' but this drawing is "
            f"{tb.drawing_number} - the tab was most likely copied from another "
            "sheet and not renamed. Harmless on the plot, but check the right "
            "sheet was issued."
        )

    # Revision-style tab name, e.g. "Rev 0" / "REV 2".
    if m := re.search(r"REV\s*([A-Z0-9]+)", name.upper()):
        tab_rev = m[1]
        if tab_rev == tb.revision.upper():
            return PASS, f"layout '{name}' matches the current revision."
        source = re.search(r"REV\s+(\S+)\s*$", tb.derived_from.upper())
        if source and source[1] == tab_rev:
            return PASS, (
                f"layout '{name}' names the source revision this drawing was "
                f"derived from."
            )
        # A tab named "Rev 0" on a drawing now at "0A" just means the tab was
        # not renamed when the alpha suffix was added -- normal practice.
        if tb.revision.upper().startswith(tab_rev):
            return PASS, (
                f"layout '{name}' predates the current rev {tb.revision} suffix."
            )
        return REVIEW, (
            f"The layout tab is named '{name}' but the drawing is at rev "
            f"{tb.revision}. Confirm the correct layout was plotted."
        )

    return NA, f"layout '{name}' follows neither naming convention."


def _filename_wellsite_verdict(fn: FileName, tb: TitleBlock) -> tuple[str, str]:
    """The Promech filename lists the wellsites; so does the title block."""
    in_name = set(WELL_TAG.findall(fn.title_part.upper()))
    in_title = set(WELL_TAG.findall(tb.title.upper()))
    if not in_name and not in_title:
        return NA, "Neither the filename nor the title lists wellsites."

    problems = []
    if missing := sorted(in_name - in_title):
        problems.append(f"in the filename but not the title block: {', '.join(missing)}")
    if extra := sorted(in_title - in_name):
        problems.append(f"in the title block but not the filename: {', '.join(extra)}")
    # A full stop between two wellsite tags is a mistyped comma.
    if stops := re.findall(r"[A-Z]{3}\d{3}\.\s+(?=[A-Z]{3}\d{3})", fn.title_part.upper()):
        problems.append(
            f"full stop instead of a comma between wellsites ({len(stops)}x) - "
            "the filename reads e.g. 'PHS022. CMN351'"
        )
    if problems:
        return REVIEW, "; ".join(problems)
    return PASS, f"both list: {', '.join(sorted(in_name))}."


def _cadfile_verdict(stem: str, drawing_number: str) -> tuple[str, str]:
    """CADFILE may cover a range of sheets, e.g. Q-4300-10-DF-188_194.DWG."""
    if not stem:
        return NO_DATA, "Could not isolate a .DWG name from the CADFILE path."
    number = _sheet_number(drawing_number)
    if number is None:
        return NO_DATA, f"CADFILE {stem}; drawing number not parseable."

    numbers = [int(n) for n in re.findall(r"\d{2,6}", stem)]
    if number in numbers:
        return PASS, f"CADFILE {stem} names sheet {number}."
    # A "188_194" style stem covers every sheet in that range.
    for lo, hi in zip(numbers, numbers[1:]):
        if lo < hi and lo <= number <= hi:
            return PASS, f"CADFILE {stem} covers sheets {lo}-{hi}, incl. {number}."
    return REVIEW, (
        f"CADFILE {stem} does not obviously cover drawing {drawing_number} - "
        "confirm the PDF was plotted from the right DWG."
    )


def _signoff_verdict(tb: TitleBlock) -> tuple[str, str]:
    summary = "; ".join(
        f"{s.role}={s.initials or '(blank)'} {s.date or '(no date)'}"
        for s in tb.signoffs
    )
    if not tb.signoffs:
        return NO_DATA, "No sign-off boxes could be read."

    populated = [s for s in tb.signoffs if s.populated]
    struck = [s for s in tb.signoffs if s.initials.strip() in {"-", "--"}]
    blank = [s for s in tb.signoffs if not s.initials.strip()]

    if _is_prelim(tb.revision) or _is_alpha(tb.revision):
        kind = "P revision" if _is_prelim(tb.revision) else "alpha revision"
        if populated:
            return REVIEW, (
                f"Rev {tb.revision} is a {kind}, where the sign-off boxes should "
                f"be hatched out or blank, but {len(populated)} are filled in. "
                f"{summary}"
            )
        return PASS, f"Rev {tb.revision} ({kind}), sign-off boxes not filled. {summary}"

    if blank:
        return FAIL, (
            f"Rev {tb.revision} is an issued revision, so every sign-off box "
            f"needs initials and a date. Blank: "
            f"{', '.join(s.role for s in blank)}. {summary}"
        )
    if struck:
        return REVIEW, (
            f"Rev {tb.revision}: {', '.join(s.role for s in struck)} struck out "
            f"with '-'. Confirm that is intended. {summary}"
        )
    return PASS, summary

