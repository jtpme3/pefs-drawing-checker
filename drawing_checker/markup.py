"""Write a marked-up copy of each drawing PDF carrying the findings.

Each finding gets a numbered callout box on the drawing at the thing it is
about, with the full finding text carried in the box annotation's comment
(popup) metadata -- no text is drawn onto the sheet and no comment page is
appended (Jordan, 2026-08-20). The drawing itself is untouched -- the markup
is annotations layered over it, so it can be turned off in any PDF reader and
the original is never modified.

Findings are located by the tags they quote. Check details name exact tokens
("RG125VB17 sits on 125-RG-2279-P153-4255"), and those tokens appear verbatim
in the sheet's text layer, so matching them is reliable without every check
having to carry coordinates.

A finding with no location at all (file metadata, a batch comparison) is
flagged in the left margin instead of being silently dropped -- every finding
must have a visible box somewhere, or the markup reads as broken.
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz

from .checks import FAIL, NO_DATA, REVIEW

# Callout and note colours, by status.
COLOURS = {
    FAIL: (0.86, 0.15, 0.15),
    REVIEW: (0.90, 0.55, 0.05),
    NO_DATA: (0.45, 0.45, 0.45),
}
LEGEND = {FAIL: "FAIL", REVIEW: "REVIEW", NO_DATA: "NO DATA"}

# Tokens worth locating on the sheet: line numbers, valve tags, wellsite and
# crossing tags, drawing numbers. Deliberately excludes ordinary words so a
# finding's prose does not scatter callouts across the drawing.
LOCATABLE = re.compile(
    r"\b(?:"
    r"\d{2,4}-[A-Z]{2}-[A-Z0-9]{3,5}-[A-Z]\d{2,3}-\d{4}"      # 125-RG-2253-P153-4255
    r"|(?:RG|RW|PG|PW)?\d{2,4}(?:VB|VF|VG)\d{1,3}[A-Z]?"      # RG125VB16
    r"|(?:HPC|RC|WC|HVC|PLC|TIP)-[A-Z]{3}\d{3}-\d+(?:-\d+)?"  # PLC-PHS021-1-1
    r"|[A-Z]{3}\d{3}(?:-\d+){0,3}"                            # PHS081, PHS081-1-2
    r"|[A-Z]-\d{4}-\d{2}-[A-Z]{2}-\d{2,6}(?:P\d{2})?"         # Q-4255-10-DF-081
    r"|DP\d{3}"                                               # DP442
    r")\b"
)

MAX_ANCHORS_PER_FINDING = 8
CALLOUT_SIZE = 13.0

# Jordan (2026-08-20, round 2): no on-sheet summary text and no appended
# comment sheet -- the numbered boxes with the full finding in each
# rectangle's comment (popup) metadata are the whole deliverable.

# Findings with no single location on the drawing (file metadata, a batch
# comparison, an empty table) are stacked in the left margin, outside the
# frame, so every number on the comment sheet has a visible flag. The stack
# starts below the numbered flag of its first entry and steps down; the
# CADFILE text occupies the margin from y ~510, so the stack wraps into the
# right margin before reaching it.
MARGIN_X = 4.0
MARGIN_W = 24.0
MARGIN_TOP = 36.0
MARGIN_STEP = 34.0
MARGIN_MAX_Y = 480.0


def _locate(sheet, detail: str) -> list[fitz.Rect]:
    """Rects on the sheet for the tags a finding quotes, in displayed space.

    Three tiers, best one wins. An exact match may sit anywhere -- a title
    block finding rightly anchors in the title block. The looser tiers are
    confined to the drawing body: the frame furniture (title block band,
    revision / reference-drawing tables, the CADFILE strip) quotes drawing
    numbers, project names and file paths too, and substring matching against
    it pinned body findings onto the reference table and the plot stamp.
    """
    tokens = []
    for token in LOCATABLE.findall(detail):
        if token not in tokens:
            tokens.append(token)
    if not tokens:
        return []

    wanted = set(tokens)

    def in_body(item) -> bool:
        return item.cy < 0.87 * sheet.height and item.x0 > 34

    exact, extended, quoted = [], [], []
    for item in sheet.items:
        text = item.text.strip()
        rect = fitz.Rect(item.x0, item.y0, item.x1, item.y1)
        if text in wanted:
            exact.append(rect)
        elif not in_body(item):
            continue
        elif any(text.startswith(t + "-") for t in wanted):
            # A label extending a quoted tag, e.g. the finding names corridor
            # CMN296-1 and the sheet draws CMN296-1-2.
            extended.append(rect)
        elif len(text) > 12 and any(
            re.search(rf"(?<![A-Z0-9-]){re.escape(t)}(?![A-Z0-9])", text)
            for t in wanted
        ):
            # A tag quoted inside a longer label, e.g. a note referencing a
            # line. The guards keep a crossing tag ("WC-CMN271-3-1") from
            # standing in for the wellsite or corridor it references.
            quoted.append(rect)

    for rects in (exact, extended, quoted):
        if rects:
            return rects[:MAX_ANCHORS_PER_FINDING]
    return []


def mark_up(report, sheet, out_path: Path, source: Path | None = None) -> tuple[Path, int, int]:
    """Write the drawing's PDF with its findings annotated.

    ``source`` is the PDF to draw on. It defaults to the sheet's own file, but
    for a DXF-derived sheet it must be the plotted PDF -- the DXF extractor
    already puts every position in that page's coordinates, so the anchors land
    without further transformation.

    Returns (path written, findings marked on the drawing, findings total).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    findings = [r for r in report.results if r.status in (FAIL, REVIEW, NO_DATA)]
    doc = fitz.open(source or sheet.pdf_path)
    try:
        page = doc[sheet.page_index if sheet.page_index < doc.page_count else 0]
        # Annotation coordinates live in unrotated page space; the extracted
        # item positions are in displayed space, so map them back.
        to_page = page.derotation_matrix
        placed = 0

        stack = 0
        for number, result in enumerate(findings, start=1):
            colour = COLOURS.get(result.status, COLOURS[NO_DATA])
            note = (
                f"[{number}] {result.status} - {result.code} {result.check}\n\n"
                f"{result.detail}\n\n"
                f"Checklist: {result.checklist_ref}"
            )
            anchors = [fitz.Rect(a) for a in result.anchors] or _locate(
                sheet, result.detail
            )
            if not anchors:
                # No single location on the drawing. Flag it in the margin
                # anyway -- a number on the comment sheet with no flag on the
                # drawing reads as a missing callout.
                y = MARGIN_TOP + stack * MARGIN_STEP
                x = MARGIN_X
                if y > MARGIN_MAX_Y:  # keep clear of the CADFILE text
                    x = sheet.width - MARGIN_X - MARGIN_W
                    y = MARGIN_TOP + (y - MARGIN_MAX_Y)
                anchors = [fitz.Rect(x, y, x + MARGIN_W, y + CALLOUT_SIZE)]
                stack += 1
                note = ("[Sheet-level finding - no single location on the "
                        "drawing, so it is flagged in the margin.]\n\n" + note)
            else:
                placed += 1
            for anchor in anchors:
                # Keep every callout on the sheet -- an approximated text box
                # can otherwise run past the page edge and the annotation
                # becomes invisible.
                anchor = anchor & page.rect
                if anchor.is_empty:
                    continue
                target = anchor * to_page
                box = page.add_rect_annot(target)
                box.set_colors(stroke=colour)
                box.set_border(width=1.2)
                box.set_info(title=f"Check {result.code}", content=note)
                box.update()

                # Numbered flag just above the tag, so the number is readable
                # even when callouts cluster. A FreeText annotation *displays*
                # its /Contents, so the note must not be attached here -- it
                # lives on the rectangle above, which shows it as a popup.
                flag = fitz.Rect(
                    target.x0, target.y0 - CALLOUT_SIZE,
                    target.x0 + CALLOUT_SIZE, target.y0 - 1,
                )
                label = page.add_freetext_annot(
                    flag, str(number), fontsize=8, fontname="hebo",
                    text_color=(1, 1, 1), fill_color=colour,
                    align=fitz.TEXT_ALIGN_CENTER,
                )
                label.set_info(title=f"Check {result.code} finding {number}")
                label.update()

        doc.save(out_path)
        return out_path, placed, len(findings)
    finally:
        doc.close()
