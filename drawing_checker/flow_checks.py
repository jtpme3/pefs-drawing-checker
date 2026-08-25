"""Flow direction on continuation flags, read from the PDF's own vector work.

A continuation flag on these PEFS is a pennant: a long box holding the target
drawing number, with a chevron point at one end.  Measured across the ARN12
DP401, ARN10 DP442 and DP094 sets, the convention is consistent:

- the chevron apex points in the direction of flow.  A flag whose apex points
  at the sheet border is flow OUT (the line leaves onto the target drawing);
  an apex pointing into the drawing body is flow IN;
- a flag pointed at *both* ends appears on trunk/corridor lines and always
  pairs with a both-ended flag on the neighbouring sheet.  It is treated as
  "no single direction shown" and excluded from direction congruence, but a
  both-ended flag meeting a single-ended one is itself an inconsistency;
- gas and water lines leave a sheet side by side, so flags come in service
  pairs -- the RAW GAS / RAW WATER label or the line number drawn along the
  flag identifies which is which, and the line number (size-service-sequence)
  identifies the same corridor on both sheets of a boundary.

This is deliberately narrower than the rejected "flow arrow on every bend and
tee" geometry check (see geometry_checks.py): here the geometry to read is a
single five-or-six-sided outline at a position the text layer already gives
us, so there is no network reconstruction and no false-positive surface.

Checks:

FLOW-01  every continuation flag shows a flow direction (per sheet)
FLOW-02  directions agree between the two sheets of each boundary (batch)
FLOW-03  gathering flow is unidirectional: a service that only ever flows
         *into* a sheet and never out has nowhere to go (per sheet)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz

from .content_checks import LINE_NUMBER, continuation_refs
from .titleblock import normalise_drawing_number

# The flag outline is a thin stroke (0.24pt on issued Origin plots). The
# ceiling sits above the 0.72pt a plot made without Origin's ctb collapses
# every weight to, and below the heavy pipe weights -- and the containment +
# span rules below do the real discrimination, so a pipe run passing through
# the flag never reads as its outline. Colour is not filtered beyond white
# (wipeouts): a ctb-less plot comes out coloured, and revision clouds are
# curves, which this reader never collects.
OUTLINE_MAX_WIDTH = 0.8
# How far outline geometry may sit outside the flag's text box (pt).
OUTLINE_MARGIN = 10.0
# How far away (box-to-box gap, pt) a service label or line number may sit and
# still belong to the flag.
LABEL_GAP = 25.0

SERVICE_LABELS = {"RAW GAS": "RG", "RAW WATER": "RW"}

OUT, IN, BOTH, NONE = "out", "in", "both", "none"


@dataclass
class Flag:
    """One continuation pennant, with its flow direction read from geometry."""

    target: str          # normalised target drawing number
    edge: str            # "L" | "R" | "T" | "B"
    direction: str       # "out" | "in" | "both" | "none"
    service: str = ""    # "RG" | "RW" | ""
    line_key: str = ""   # "250-RG-5619" -- same corridor on both sheets
    rect: tuple = ()     # displayed page coords, for markup anchoring

    def describe(self) -> str:
        svc = {"RG": "gas", "RW": "water"}.get(self.service, "unknown service")
        name = self.line_key or svc
        return f"{name} -> {self.target}"


def _flag_edge(item, width: float, height: float, horiz: bool) -> str:
    """Which border the flag leaves through.

    The flag's own orientation decides the axis -- a flag always runs
    perpendicular to its border (vertical flags on the top/bottom edges,
    horizontal flags on the left/right). Position bands alone misread a
    corner: DF-1157P02's bottom-left water pennant sat inside the left band,
    read as an L-edge flag, and its down-pointing apex then decoded as flow
    IN instead of OUT (verified by eye, 2026-08-20).
    """
    if horiz:
        return "L" if item.cx < width / 2 else "R"
    return "T" if item.cy < height / 2 else "B"


def _outline_paths(page):
    """Thin black stroked polylines, as displayed-coordinate segment lists."""
    rot = page.rotation_matrix
    paths = []
    for d in page.get_drawings():
        if d["type"] != "s":
            continue
        if not d.get("width") or d["width"] > OUTLINE_MAX_WIDTH:
            continue
        if d.get("color") and min(d["color"]) > 0.85:  # white: a wipeout edge
            continue
        segs = [(p0 * rot, p1 * rot) for kind, p0, p1, *_ in
                ((it[0], it[1], it[2]) for it in d["items"] if it[0] == "l")]
        if segs:
            paths.append(segs)
    return paths


def _classify_ends(paths, item) -> tuple[bool, str, str] | None:
    """(is_horizontal, low_end, high_end) for the pennant around a text item.

    Ends are "point" (chevron apex), "flat" (square cap) or "?" (nothing
    recognised).  Returns None when no outline as long as the flag is found
    nearby -- the item is then a detail callout, not a pennant.
    """
    box = fitz.Rect(item.x0 - OUTLINE_MARGIN, item.y0 - OUTLINE_MARGIN,
                    item.x1 + OUTLINE_MARGIN, item.y1 + OUTLINE_MARGIN)
    horiz = (item.x1 - item.x0) > (item.y1 - item.y0)
    long_extent = max(item.x1 - item.x0, item.y1 - item.y0)
    mid = ((item.x0 + item.x1) / 2, (item.y0 + item.y1) / 2)

    diag = {"low": 0, "high": 0}
    flat = {"low": 0, "high": 0}
    found_outline = False
    for segs in paths:
        pts = [p for seg in segs for p in seg]
        if not all(box.contains(p) for p in pts):
            continue
        span = max(
            max(p.x for p in pts) - min(p.x for p in pts),
            max(p.y for p in pts) - min(p.y for p in pts),
        )
        if span < 0.5 * long_extent:
            continue  # text stroke or leader, not the outline
        found_outline = True
        for p0, p1 in segs:
            dx, dy = abs(p1.x - p0.x), abs(p1.y - p0.y)
            along = (p0.x + p1.x) / 2 if horiz else (p0.y + p1.y) / 2
            end = "low" if along < (mid[0] if horiz else mid[1]) else "high"
            if dx > 1.5 and dy > 1.5:
                diag[end] += 1
            elif (dy > dx) == horiz:  # perpendicular to the long axis: a cap
                flat[end] += 1

    if not found_outline:
        return None

    def end(name: str) -> str:
        # A chevron is two diagonals meeting at the apex.
        if diag[name] >= 2:
            return "point"
        return "flat" if flat[name] else "?"

    return horiz, end("low"), end("high")


def _direction(edge: str, horiz: bool, low: str, high: str) -> str:
    """Map chevron ends to flow relative to this sheet.

    "low" is the left end of a horizontal flag and the top end of a vertical
    one.  Apex toward the border = flow out; apex into the body = flow in.
    """
    points = [e for e, kind in (("low", low), ("high", high)) if kind == "point"]
    if len(points) == 2:
        return BOTH
    if not points:
        return NONE
    towards = points[0]
    if horiz:
        pointing = "L" if towards == "low" else "R"
    else:
        pointing = "T" if towards == "low" else "B"
    return OUT if pointing == edge else IN


def _nearby_text(sheet, item, want_line_number: bool):
    """Nearest service label or line number belonging to a flag."""
    best, best_gap = "", LABEL_GAP
    for other in sheet.items:
        text = other.text.strip().upper()
        if want_line_number:
            if "-RG-" not in text and "-RW-" not in text:
                continue
        elif text not in SERVICE_LABELS:
            continue
        gap = (max(0.0, max(item.x0, other.x0) - min(item.x1, other.x1))
               + max(0.0, max(item.y0, other.y0) - min(item.y1, other.y1)))
        if gap < best_gap:
            best_gap, best = gap, text
    return best


def read_flags(sheet) -> list[Flag] | None:
    """Every pennant flag on a sheet, or None when the geometry is not readable
    (a DXF-derived sheet, or the PDF cannot be reopened)."""
    if getattr(sheet, "dxf", None) is not None:
        return None
    path = Path(sheet.pdf_path)
    if path.suffix.lower() != ".pdf" or not path.exists():
        return None
    try:
        doc = fitz.open(path)
        page = doc[sheet.page_index]
    except Exception:
        return None
    try:
        paths = _outline_paths(page)
        flags: list[Flag] = []
        for target, item in continuation_refs(sheet):
            shape = _classify_ends(paths, item)
            if shape is None:
                continue  # no pennant outline: a detail callout, not a flag
            horiz, low, high = shape
            if low == "?" and high == "?":
                continue  # outline unreadable; do not guess
            edge = _flag_edge(item, sheet.width, sheet.height, horiz)
            line = _nearby_text(sheet, item, want_line_number=True)
            service = ""
            line_key = ""
            if line and (m := LINE_NUMBER.search(line)):
                service = m["service"]
                if not re.fullmatch(r"[X?]+", m["seq"], re.IGNORECASE):
                    line_key = f"{m['size']}-{m['service']}-{m['seq']}"
            if not service:
                service = SERVICE_LABELS.get(
                    _nearby_text(sheet, item, want_line_number=False), "")
            flags.append(Flag(
                target=normalise_drawing_number(target),
                edge=edge,
                direction=_direction(edge, horiz, low, high),
                service=service,
                line_key=line_key,
                rect=(item.x0, item.y0, item.x1, item.y1),
            ))
        return flags
    finally:
        doc.close()


def _base(number: str) -> str:
    return re.sub(r"P\d{2}$", "", normalise_drawing_number(number or ""))


def check_flow_direction(entries) -> None:
    """FLOW-01/02/03, appended to each report in place."""
    from .checks import FAIL, NA, NO_DATA, PASS, REVIEW, Result

    checked = [(r, s, t) for r, s, t in entries if not r.skipped]
    if not checked:
        return

    from .content_checks import _sheet_number

    ref = "Rows 20, 21, 28, 67"
    per_sheet: dict[str, list[Flag] | None] = {}
    names: dict[str, str] = {}
    keys: dict[str, str] = {}  # report.label -> key, so all passes agree
    for report, sheet, tb in checked:
        number = _sheet_number(report, tb)
        mine = _base(number) if number else report.label
        keys[report.label] = mine
        names[mine] = number or report.label
        flags = read_flags(sheet)
        if flags is not None:
            flags = [f for f in flags if _base(f.target) != mine]
        per_sheet[mine] = flags

    # -- FLOW-01: every flag shows a direction --------------------------------
    for report, sheet, tb in checked:
        mine = keys[report.label]
        flags = per_sheet[mine]
        title = "Continuation flags show flow direction"
        if flags is None:
            report.results.append(Result(
                "FLOW-01", "Drawing Body", title, NO_DATA,
                "Flow direction is read from the PDF's vector geometry, which "
                "is not available for this sheet.", ref))
            continue
        if not flags:
            report.results.append(Result(
                "FLOW-01", "Drawing Body", title, NA,
                "No continuation flags on this sheet.", ref))
            continue
        blank = [f for f in flags if f.direction == NONE]
        counts = (f"{sum(1 for f in flags if f.direction == OUT)} out, "
                  f"{sum(1 for f in flags if f.direction == IN)} in, "
                  f"{sum(1 for f in flags if f.direction == BOTH)} both-ended")
        if blank:
            report.results.append(Result(
                "FLOW-01", "Drawing Body", title, FAIL,
                f"{len(blank)} of {len(flags)} continuation flag(s) carry no "
                "flow point at either end: "
                + "; ".join(f.describe() for f in blank)
                + f". Directions on the rest: {counts}.", ref,
                anchors=[f.rect for f in blank]))
        else:
            report.results.append(Result(
                "FLOW-01", "Drawing Body", title, PASS,
                f"{len(flags)} continuation flag(s), all showing flow "
                f"direction ({counts}).", ref))

    # -- FLOW-02: directions agree across each boundary -----------------------
    for report, sheet, tb in checked:
        mine = keys[report.label]
        flags = per_sheet[mine]
        title = "Continuation flow directions agree between sheets"
        if flags is None:
            continue  # FLOW-01 already reported NO DATA
        neighbours = sorted({
            _base(f.target) for f in flags
            if _base(f.target) in per_sheet and per_sheet[_base(f.target)] is not None
        })
        if not neighbours:
            report.results.append(Result(
                "FLOW-02", "Drawing Body", title, NA,
                "No continuation onto another readable sheet in this set.", ref))
            continue

        problems, agreements, anchors = [], [], []
        worst = PASS
        for other in neighbours:
            ours = [f for f in flags if _base(f.target) == other]
            theirs = [f for f in per_sheet[other] if _base(f.target) == mine]
            verdicts = _boundary_verdicts(names[other], ours, theirs)
            for status, message, flag in verdicts:
                if status == PASS:
                    agreements.append(message)
                else:
                    problems.append(message)
                    if flag is not None:
                        anchors.append(flag.rect)
                    if status == FAIL:
                        worst = FAIL
                    elif worst != FAIL:
                        worst = REVIEW
        if problems:
            report.results.append(Result(
                "FLOW-02", "Drawing Body", title, worst,
                "; ".join(problems), ref, anchors=anchors))
        else:
            report.results.append(Result(
                "FLOW-02", "Drawing Body", title, PASS,
                "Flow directions agree with "
                + "; ".join(agreements) + ".", ref))

    # -- FLOW-03: gathering flow is unidirectional ----------------------------
    for report, sheet, tb in checked:
        mine = keys[report.label]
        flags = per_sheet[mine]
        title = "Flow leaves the sheet (wellsites gather toward the trunkline)"
        if flags is None:
            continue
        directional = [f for f in flags if f.direction in (OUT, IN)]
        if not directional:
            report.results.append(Result(
                "FLOW-03", "Drawing Body", title, NA,
                "No single-direction continuation flags on this sheet.", ref))
            continue
        stuck = []
        for service in ("RG", "RW"):
            svc = [f for f in flags if f.service == service]
            if not svc:
                continue
            if (all(f.direction == IN for f in svc if f.direction != NONE)
                    and any(f.direction == IN for f in svc)):
                stuck.append((service, svc))
        if stuck:
            report.results.append(Result(
                "FLOW-03", "Drawing Body", title, REVIEW,
                "; ".join(
                    f"every {'gas' if s == 'RG' else 'water'} continuation "
                    f"flag points INTO this sheet "
                    f"({', '.join(f.describe() for f in fl)}) - the flow has "
                    "no way off the sheet unless it terminates at a facility "
                    "drawn here"
                    for s, fl in stuck
                ), ref,
                anchors=[f.rect for _, fl in stuck for f in fl]))
        else:
            report.results.append(Result(
                "FLOW-03", "Drawing Body", title, PASS,
                "Each service with continuation flags has a flow path off "
                "the sheet.", ref))


def _boundary_verdicts(other_name: str, ours: list[Flag], theirs: list[Flag]):
    """Compare the two sides of one sheet boundary.

    Returns (status, message, offending-flag-or-None) tuples.  Matching is by
    corridor line number where both sheets label the flag with one, then by
    service; a head-on contradiction is a FAIL, anything structural (missing
    flag, single-ended meeting both-ended) is a REVIEW.
    """
    from .checks import FAIL, PASS, REVIEW

    verdicts = []
    ours_left, theirs_left = list(ours), list(theirs)

    # 1. corridor-by-corridor, where the line number is on both sheets
    our_keys = {f.line_key: f for f in ours if f.line_key}
    their_keys = {f.line_key: f for f in theirs if f.line_key}
    for key in sorted(set(our_keys) & set(their_keys)):
        a, b = our_keys[key], their_keys[key]
        ours_left.remove(a)
        theirs_left.remove(b)
        verdicts.append(_pair_verdict(other_name, key, a, b))

    # 2. remaining flags, by service
    for service in ("RG", "RW", ""):
        a_svc = [f for f in ours_left if f.service == service]
        b_svc = [f for f in theirs_left if f.service == service]
        if not a_svc and not b_svc:
            continue
        label = {"RG": "gas", "RW": "water"}.get(service, "unidentified-service")
        if len(a_svc) != len(b_svc):
            verdicts.append((
                REVIEW,
                f"{len(a_svc)} {label} flag(s) to {other_name} but "
                f"{len(b_svc)} matching flag(s) back from it - a flag or its "
                "service label is missing on one sheet",
                a_svc[0] if a_svc else None))
            continue
        a_dirs = sorted(f.direction for f in a_svc)
        b_complement = sorted(
            {OUT: IN, IN: OUT}.get(f.direction, f.direction) for f in b_svc
        )
        if a_dirs == b_complement:
            verdicts.append((PASS, f"{other_name} ({len(a_svc)} {label})", None))
        elif (all(f.direction == OUT for f in a_svc)
                and all(f.direction == OUT for f in b_svc)):
            verdicts.append((
                FAIL,
                f"head-on {label} flow with {other_name}: both sheets show "
                "the line flowing OUT toward each other",
                a_svc[0]))
        elif (all(f.direction == IN for f in a_svc)
                and all(f.direction == IN for f in b_svc)):
            verdicts.append((
                FAIL,
                f"orphan {label} flow with {other_name}: both sheets show "
                "the line flowing IN from the other, so neither is its source",
                a_svc[0]))
        else:
            verdicts.append((
                REVIEW,
                f"{label} flow with {other_name} is inconsistent: this sheet "
                f"shows {', '.join(a_dirs)}; the matching flags there show "
                f"{', '.join(sorted(f.direction for f in b_svc))}",
                a_svc[0]))
    return verdicts


def _pair_verdict(other_name: str, key: str, a: Flag, b: Flag):
    """Verdict for one corridor matched by line number across a boundary."""
    from .checks import FAIL, PASS, REVIEW

    if a.direction == OUT and b.direction == IN:
        return (PASS, f"{other_name} ({key})", None)
    if a.direction == IN and b.direction == OUT:
        return (PASS, f"{other_name} ({key})", None)
    if a.direction == BOTH and b.direction == BOTH:
        return (PASS, f"{other_name} ({key}, both-ended)", None)
    if a.direction == OUT and b.direction == OUT:
        return (FAIL,
                f"line {key}: this sheet and {other_name} both show it "
                "flowing OUT toward each other", a)
    if a.direction == IN and b.direction == IN:
        return (FAIL,
                f"line {key}: this sheet and {other_name} both show it "
                "flowing IN from the other, so neither is its source", a)
    return (REVIEW,
            f"line {key}: this sheet shows '{a.direction}' but {other_name} "
            f"shows '{b.direction}' - one of the two flags is drawn with the "
            "wrong pennant", a)
