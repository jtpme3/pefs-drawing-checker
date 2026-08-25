"""Checks on the drawn geometry rather than the text.

Only two things in the vector data turned out to be trustworthy on these
plots. What was measured and rejected is recorded at the bottom of this
module, so it does not get re-attempted.
"""

from __future__ import annotations

import re

import fitz

RED = (1.0, 0.0, 0.0)
COLOUR_TOLERANCE = 0.15
# A revision cloud is a run of small arcs. Symbols (valves, vessels) use curves
# too, so a handful of curves is not a cloud -- this is the floor for calling
# red geometry "clouding".
MIN_CLOUD_CURVES = 40
# Distance from the sheet edge within which text is a border grid reference
# (A-H down the sides, 1-12 along the top and bottom) rather than drawing
# content.
BORDER_MARGIN = 45


def _is_red(colour) -> bool:
    if not colour or len(colour) < 3:
        return False
    r, g, b = colour[:3]
    return r > 0.7 and g < COLOUR_TOLERANCE + 0.25 and b < COLOUR_TOLERANCE + 0.25


def measure(pdf_path, page_index: int) -> dict:
    """Vector statistics for one page, read straight from the PDF."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        widths: dict[float, int] = {}
        red_curves = 0
        red_rects = []
        for drawing in page.get_drawings():
            width = drawing.get("width") or 0.0
            if width:
                widths[round(width, 2)] = widths.get(round(width, 2), 0) + 1
            if _is_red(drawing.get("color")):
                curves = sum(1 for item in drawing["items"] if item[0] == "c")
                if curves:
                    red_curves += curves
                    red_rects.append(drawing["rect"])
        return {
            "widths": widths,
            "red_curves": red_curves,
            "red_rects": red_rects,
        }
    finally:
        doc.close()


def run_geometry_checks(sheet, tb, add) -> None:
    from .checks import FAIL, NA, PASS, REVIEW

    if sheet.from_dxf:
        # A DXF says outright which entities are revision clouds, and DXF-04
        # answers the line weight question better via layers.
        _check_clouding_dxf(sheet, tb, add)
        return

    stats = measure(sheet.pdf_path, sheet.page_index)
    _check_clouding(sheet, tb, stats, add)
    _check_line_weights(stats, add)


CLOUD_LAYER = re.compile(r"REV[_\s-]*CLOUD|CLOUD", re.IGNORECASE)


def _check_clouding_dxf(sheet, tb, add) -> None:
    from .checks import FAIL, NA, PASS

    ref = "Rows 33, 58"
    title = "Changes clouded for this revision"
    clouds = [s for s in sheet.dxf.segments if CLOUD_LAYER.search(s.layer)]
    markers = _revision_markers(sheet, tb.revision)

    if _is_first_issue(tb):
        add("GEO-01", "Drawing Body", title, NA,
            f"Rev {tb.revision} is the first issue - nothing to cloud.", ref)
    elif clouds or markers:
        detail = []
        if clouds:
            detail.append(f"{len(clouds)} segment(s) on a revision cloud layer")
        if markers:
            detail.append(f"{len(markers)} '{tb.revision}' revision marker(s)")
        add("GEO-01", "Drawing Body", title, PASS, "; ".join(detail), ref)
    else:
        add("GEO-01", "Drawing Body", title, FAIL,
            f"Rev {tb.revision} follows earlier revisions but the drawing has "
            "no revision cloud layer content and no revision marker.", ref)


def _revision_markers(sheet, revision: str) -> list:
    """Revision triangles/squares: the revision character on its own in the body.

    Border grid references are excluded -- an "A" revision would otherwise
    match the A-H zone letters printed down both edges of every sheet.
    """
    wanted = revision.strip().upper()
    if not wanted:
        return []
    return [
        i for i in sheet.items
        if i.text.strip().upper() == wanted
        and BORDER_MARGIN < i.cx < sheet.width - BORDER_MARGIN
        and BORDER_MARGIN < i.cy < sheet.height - BORDER_MARGIN
        and not (i.cx > 0.66 * sheet.width and i.cy > 0.85 * sheet.height)
    ]


def _is_first_issue(tb) -> bool:
    """True when nothing precedes this revision, so there is nothing to cloud.

    A P revision derived from an approved operational or project drawing is
    never a first issue however short its own revision history -- its whole
    purpose is to show changes against that source, so it must be clouded.
    """
    if tb.derived_from:
        return False
    return len(tb.revision_history) <= 1


def _check_clouding(sheet, tb, stats, add) -> None:
    from .checks import FAIL, NA, PASS

    ref = "Rows 33, 58"
    title = "Changes clouded for this revision"
    is_prelim = "P" in tb.drawing_number.upper().split("-")[-1]
    markers = _revision_markers(sheet, tb.revision)
    red_curves = stats["red_curves"]

    if _is_first_issue(tb):
        add("GEO-01", "Drawing Body", title, NA,
            f"Rev {tb.revision} is the first issue - nothing to cloud.", ref)
        return

    if is_prelim:
        # Project P-revs are clouded in red, per checklist row 33.
        if red_curves >= MIN_CLOUD_CURVES:
            add("GEO-01", "Drawing Body", title, PASS,
                f"{red_curves} red cloud arcs found, as expected on a P revision.",
                ref)
        else:
            add("GEO-01", "Drawing Body", title, FAIL,
                f"{tb.drawing_number} is a P revision, which should carry red "
                f"project revision clouds, but only {red_curves} red arcs were "
                "found. Either the changes are not clouded, or the clouds are "
                "not in red.", ref)
        return

    # Numbered revisions use black clouds with revision triangles.
    if markers:
        add("GEO-01", "Drawing Body", title, PASS,
            f"{len(markers)} revision marker(s) reading '{tb.revision}' found in "
            "the drawing body.", ref)
    else:
        add("GEO-01", "Drawing Body", title, FAIL,
            f"Rev {tb.revision} follows earlier revisions "
            f"({', '.join(r.revision for r in tb.revision_history[1:])}) but no "
            f"revision marker reading '{tb.revision}' was found in the drawing "
            "body, so the changes appear not to be clouded.", ref)


def _check_line_weights(stats, add) -> None:
    """Gas heavy / water thin means a sheet must use more than one weight."""
    from .checks import NO_DATA, PASS, REVIEW

    widths = stats["widths"]
    ref = "Rows 32, 70"
    title = "Line weights differentiate gas from water"
    if not widths:
        add("GEO-02", "Drawing Body", title, NO_DATA,
            "No stroked geometry found.", ref)
        return

    # Ignore weights used only incidentally (border, hatching).
    total = sum(widths.values())
    substantive = {w: n for w, n in widths.items() if n >= max(20, total * 0.02)}
    summary = ", ".join(f"{w}pt x{n}" for w, n in sorted(substantive.items()))
    if len(substantive) >= 2:
        add("GEO-02", "Drawing Body", title, PASS,
            f"{len(substantive)} stroke weights in substantive use: {summary}.", ref)
    else:
        add("GEO-02", "Drawing Body", title, REVIEW,
            f"Only one stroke weight is in substantive use ({summary}), so gas "
            "and water lines are not visually distinguished.", ref)


# -- Measured and deliberately not implemented --------------------------------
#
# Flow arrow on every bend and tee (rows 28, 68). Would need the pipe network
# reconstructed from ~9,000 stroked paths and each arrowhead matched to a
# vertex. Every prototype produced false positives on legitimate straight runs;
# a check that cries wolf on a correct drawing is worse than no check.
#
# Property boundaries shown dashed (rows 25, 60). Not possible from these PDFs:
# every path reports dashes "[] 0" because AutoCAD emits dashed linetypes as
# separate short segments rather than as a PDF dash array.
#
# Tag clutter / overlapping labels (rows 30, 72). The SHX capture rect is the
# annotation square, not a tight glyph box, so stacked label lines such as
# "RG125VB16" above "PHS100-1-1" report as 100% overlapping. Measured 35-91
# "overlaps" per sheet on drawings with no actual clutter.
