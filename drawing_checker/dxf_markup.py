"""Write the findings back into a copy of the DXF, as review markup.

Everything lands in **paper space**, on layers of its own, for three reasons:

* page coordinates map straight to paper space, so a callout sits exactly where
  the equivalent PDF callout would;
* the markup prints with the sheet;
* it is trivially removable -- freeze or purge the ``CHECK-*`` layers and the
  drawing is back to how it was. The source DXF is never touched; a copy is
  written.

A second layout, ``CHECK COMMENTS``, carries the numbered schedule, so the
comments can be plotted as their own sheet.
"""

from __future__ import annotations

import re
from pathlib import Path

import ezdxf

from .checks import FAIL, NO_DATA, REVIEW

LAYERS = {
    FAIL: ("CHECK-FAIL", 1),        # ACI 1  red
    REVIEW: ("CHECK-REVIEW", 30),   # ACI 30 orange
    NO_DATA: ("CHECK-INFO", 8),     # ACI 8  grey
}
SCHEDULE_LAYOUT = "CHECK COMMENTS"
# Callout box padding and label size, in paper units.
PADDING = 1.5
LABEL_HEIGHT = 4.0
MAX_CALLOUTS_PER_FINDING = 8
# The on-sheet label is a pointer to the schedule, not the comment itself.
NOTE_MAX_CHARS = 58


def _ensure_layers(doc) -> None:
    for name, colour in LAYERS.values():
        if name not in doc.layers:
            doc.layers.add(name, color=colour)
        else:
            doc.layers.get(name).dxf.color = colour


def _purge_previous(layout) -> int:
    """Remove markup from an earlier run so re-checking does not stack up."""
    names = {name for name, _ in LAYERS.values()}
    doomed = [e for e in layout if e.dxf.layer in names]
    for entity in doomed:
        layout.delete_entity(entity)
    return len(doomed)


def mark_up_dxf(report, sheet, out_path: Path) -> tuple[Path, int, int]:
    """Write ``sheet``'s DXF with its findings drawn on it.

    Returns (path written, findings drawn on the sheet, findings total).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    findings = [r for r in report.results if r.status in (FAIL, REVIEW, NO_DATA)]
    transform = sheet.transform

    doc = ezdxf.readfile(sheet.pdf_path)
    _ensure_layers(doc)

    layout_name = getattr(sheet.dxf, "layout", "") or ""
    layout = (
        doc.layout(layout_name)
        if layout_name in doc.layout_names()
        else next(iter(doc.layouts_and_blocks()), doc.modelspace())
    )
    _purge_previous(layout)

    drawn = 0
    for number, result in enumerate(findings, start=1):
        layer, _ = LAYERS.get(result.status, LAYERS[NO_DATA])
        anchors = result.anchors or _locate(sheet, result.detail)
        if not anchors:
            continue
        drawn += 1
        for x0, y0, x1, y1 in anchors[:MAX_CALLOUTS_PER_FINDING]:
            _draw_callout(layout, transform, layer, number, result, x0, y0, x1, y1)

    _write_schedule(doc, report, findings)
    doc.saveas(out_path)
    return out_path, drawn, len(findings)


def _draw_callout(layout, transform, layer, number, result, x0, y0, x1, y1) -> None:
    """A box round the item, a numbered tag, and the comment as hover text."""
    # Page coordinates run down the sheet, paper coordinates run up it, so the
    # top and bottom edges swap on the way through.
    left, bottom = transform.page_to_paper(x0, y1)
    right, top = transform.page_to_paper(x1, y0)
    left, right = min(left, right) - PADDING, max(left, right) + PADDING
    bottom, top = min(bottom, top) - PADDING, max(bottom, top) + PADDING

    layout.add_lwpolyline(
        [(left, bottom), (right, bottom), (right, top), (left, top)],
        close=True,
        dxfattribs={"layer": layer},
    )
    tag = layout.add_text(
        str(number),
        dxfattribs={"layer": layer, "height": LABEL_HEIGHT},
    )
    tag.set_placement((left, top + PADDING))

    # A short label beside the box, so the sheet reads without flipping to the
    # schedule -- but kept to one line, because the full detail written here
    # runs straight across the drawing and buries it.
    summary = f"[{number}] {result.status} {result.code} {result.check}"
    if len(summary) > NOTE_MAX_CHARS:
        summary = summary[: NOTE_MAX_CHARS - 1].rstrip() + "…"
    note = layout.add_text(
        _escape(summary),
        dxfattribs={"layer": layer, "height": LABEL_HEIGHT * 0.6},
    )
    note.set_placement((right + PADDING * 2, top - LABEL_HEIGHT * 0.6))


def _write_schedule(doc, report, findings) -> None:
    """A numbered comment schedule on its own layout, ready to plot."""
    if SCHEDULE_LAYOUT in doc.layout_names():
        doc.layouts.delete(SCHEDULE_LAYOUT)
    layout = doc.layouts.new(SCHEDULE_LAYOUT)
    layout.page_setup(size=(420, 297), margins=(10, 10, 10, 10), units="mm")

    y = 280.0
    layout.add_text(
        "DRAWING CHECK COMMENTS",
        dxfattribs={"layer": "CHECK-FAIL", "height": 6.0},
    ).set_placement((15, y))
    y -= 9
    header = (
        f"{report.drawing_number or report.filename}"
        f"{'  Rev ' + report.revision if report.revision else ''}"
        f"   -   {report.filename}"
    )
    layout.add_text(header, dxfattribs={"height": 3.0}).set_placement((15, y))
    y -= 6
    counts = ", ".join(
        f"{sum(1 for r in findings if r.status == s)} {s}"
        for s in (FAIL, REVIEW, NO_DATA)
        if any(r.status == s for r in findings)
    )
    layout.add_text(
        counts or "No findings - every check passed.",
        dxfattribs={"height": 3.0},
    ).set_placement((15, y))
    y -= 8

    for number, result in enumerate(findings, start=1):
        layer, _ = LAYERS.get(result.status, LAYERS[NO_DATA])
        if y < 20:
            break
        text = (
            f"{number}. {result.status}  {result.code}  {result.check}\\P"
            f"{result.detail}\\P"
            f"Checklist {result.checklist_ref}"
        )
        block = layout.add_mtext(
            _escape(text, keep_breaks=True),
            dxfattribs={"layer": layer, "char_height": 2.4, "width": 390.0},
        )
        block.set_location((15, y))
        # MTEXT height is not known until it is rendered, so step down by an
        # estimate from the wrapped line count.
        lines = 2 + len(result.detail) // 150
        y -= 3.2 * lines + 5

    if not findings:
        layout.add_text(
            "Nothing to comment on.", dxfattribs={"height": 3.0}
        ).set_placement((15, y))


MTEXT_SPECIALS = re.compile(r"([\\{}])")


def _escape(text: str, keep_breaks: bool = False) -> str:
    """MTEXT treats backslash and braces as formatting codes."""
    if keep_breaks:
        parts = text.split("\\P")
        return "\\P".join(MTEXT_SPECIALS.sub(r"\\\1", p) for p in parts)
    return MTEXT_SPECIALS.sub(r"\\\1", text)


def _locate(sheet, detail: str):
    """Reuse the PDF markup's tag locator, in page coordinates."""
    from .markup import _locate as locate_rects

    return [(r.x0, r.y0, r.x1, r.y1) for r in locate_rects(sheet, detail)]
