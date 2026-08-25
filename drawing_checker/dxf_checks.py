"""Checks that need real drawing objects, so only run on DXF-derived sheets.

Everything here answers a question the PDF cannot: what layer is this on, what
linetype, which block is it, what does it connect to. The PDF equivalents were
measured and rejected -- see the note at the bottom of ``geometry_checks.py``.
"""

from __future__ import annotations

import math
import re

from .dxf_extract import (
    CONTINUATION, CROSSING, FLOW_ARROW, GAS_LAYERS, PIPE_LAYERS,
    PROPERTY_BOUNDARY, REDUCER, SPEC_BREAK, TIE_IN, VALVE, WATER_LAYERS,
)
from .titleblock import ORIGIN_DWG_NO, normalise_drawing_number

# A linetype counts as dashed if its name says so -- AutoCAD ships DASHED,
# HIDDEN, DASHDOT and the drawings define their own variants on those names.
DASHED_LINETYPE = re.compile(r"DASH|HIDDEN|DOT|CENTER|PHANTOM", re.IGNORECASE)
# Two endpoints this close (in points on the plotted page) are the same node.
SNAP = 2.5
# A run that stops this close to a symbol is broken *by* that symbol, not left
# dangling -- normal P&ID practice.
SYMBOL_BRIDGE = 14.0
# A run reaching this close to the sheet edge leaves via a matchline.
EDGE_MARGIN = 30.0


def run_dxf_checks(sheet, tb, add) -> None:
    from .checks import FAIL, NA, PASS, REVIEW

    model = sheet.dxf
    if model is None:
        return

    _check_blocks_recognised(model, add)
    _check_flow_arrows(model, add)
    _check_property_boundaries(model, add)
    _check_service_layers(model, add)
    _check_continuation_line_numbers(model, add)
    _check_network(model, add, sheet.width, sheet.height)


def _check_blocks_recognised(model, add) -> None:
    """Unrecognised blocks mean checks are silently skipping symbols."""
    from .checks import PASS, REVIEW

    ref = "n/a - tool coverage"
    title = "Drawing symbols recognised"
    unknown = model.unrecognised_blocks
    known = sum(1 for b in model.blocks if b.kind)
    coverage = known / len(model.blocks) if model.blocks else 1.0
    if not unknown or coverage >= 0.75:
        add("DXF-01", "Extraction", title, PASS,
            f"{known} of {len(model.blocks)} block(s) classified ({coverage:.0%})."
            + (f" Not in the symbol table: "
               f"{', '.join(sorted(unknown)[:6])}." if unknown else ""),
            ref)
    else:
        listed = ", ".join(
            f"{name} x{n}" for name, n in sorted(unknown.items(), key=lambda kv: -kv[1])[:8]
        )
        add("DXF-01", "Extraction", title, REVIEW,
            f"{known} of {len(model.blocks)} block(s) classified. Not in the "
            f"symbol table, so not checked: {listed}. Add them to BLOCK_KINDS "
            "in dxf_extract.py if they matter.", ref)


def _check_flow_arrows(model, add) -> None:
    """Checklist rows 28 and 68: flow direction must be shown."""
    from .checks import FAIL, NA, PASS

    ref = "Rows 28, 67, 68"
    title = "Flow direction shown on the pipework"
    arrows = model.of_kind(FLOW_ARROW)
    pipe = [s for s in model.segments if PIPE_LAYERS.match(s.layer)]
    if not pipe:
        add("DXF-02", "Drawing Body", title, NA,
            "No process pipe on this sheet.", ref)
    elif not arrows:
        add("DXF-02", "Drawing Body", title, FAIL,
            f"{len(pipe)} pipe segment(s) drawn but no flow arrow symbol "
            "anywhere on the sheet.", ref)
    else:
        add("DXF-02", "Drawing Body", title, PASS,
            f"{len(arrows)} flow arrow(s) over {len(pipe)} pipe segment(s).", ref)


def _check_property_boundaries(model, add) -> None:
    """Checklist rows 25 and 60: property boundaries shown as dashed lines."""
    from .checks import FAIL, NA, PASS

    ref = "Rows 25, 60"
    title = "Property boundaries drawn dashed"
    boundary = [
        s for s in model.segments
        if "PROPERTY" in s.layer.upper() or "PROP BOUNDARY" in s.layer.upper()
    ]
    if not boundary:
        add("DXF-03", "Drawing Body", title, NA,
            "No property boundary linework on this sheet.", ref)
        return
    solid = [s for s in boundary if not DASHED_LINETYPE.search(s.linetype)]
    if solid:
        kinds = sorted({s.linetype for s in solid})
        add("DXF-03", "Drawing Body", title, FAIL,
            f"{len(solid)} of {len(boundary)} property boundary segment(s) are "
            f"not dashed (linetype {', '.join(kinds)}).", ref)
    else:
        add("DXF-03", "Drawing Body", title, PASS,
            f"all {len(boundary)} property boundary segment(s) dashed "
            f"({sorted({s.linetype for s in boundary})[0]}).", ref)


def _check_service_layers(model, add) -> None:
    """Checklist rows 32 and 70: gas and water must be distinguishable.

    On a DXF that is a layer question rather than a line-weight one, which is
    both more reliable and closer to what the drafter actually controls.
    """
    from .checks import NA, PASS, REVIEW

    ref = "Rows 32, 70"
    title = "Gas and water pipework on their own layers"
    pipe = [s for s in model.segments if PIPE_LAYERS.match(s.layer)]
    if not pipe:
        add("DXF-04", "Drawing Body", title, NA, "No process pipe on this sheet.", ref)
        return

    gas = {s.layer for s in pipe if GAS_LAYERS.match(s.layer)}
    water = {s.layer for s in pipe if WATER_LAYERS.match(s.layer)}
    # PROCESS1 / PROCESS2 are the house convention for trunkline runs, which
    # take their service from the line number rather than the layer. Every
    # drawing in the set uses them, so they are not a finding.
    other = sorted(
        layer for layer in ({s.layer for s in pipe} - gas - water)
        if not re.fullmatch(r"PROCESS\s*\d+", layer, re.IGNORECASE)
    )

    problems = []
    # "PROCESS - GAS" and "PROCESS-GAS" on the same job is a drafting
    # inconsistency in its own right.
    for name, group in (("gas", gas), ("water", water)):
        if len({re.sub(r"[\s-]+", "", g).upper() for g in group}) < len(group):
            problems.append(f"{name} pipe split across {', '.join(sorted(group))}")
    if other:
        problems.append(f"pipe on unexpected layer(s): {', '.join(other)}")

    add("DXF-04", "Drawing Body", title,
        REVIEW if problems else PASS,
        "; ".join(problems) or
        f"gas on {', '.join(sorted(gas)) or 'the trunkline layers'}; "
        f"water on {', '.join(sorted(water)) or 'the trunkline layers'}.",
        ref)


def _check_continuation_line_numbers(model, add) -> None:
    """A continuation flag with no line number leaves the reader guessing."""
    from .checks import NA, PASS, REVIEW

    ref = "Rows 20, 21, 52"
    title = "Continuation flags carry a line number"
    flags = model.of_kind(CONTINUATION)
    if not flags:
        add("DXF-05", "Drawing Body", title, NA,
            "No continuation flags on this sheet.", ref)
        return
    with_field = [f for f in flags if any(
        t.upper() == "LINENO" for t in f.attributes
    )]
    blank = [f for f in with_field if not f.attr("LINENO")]
    if not with_field:
        add("DXF-05", "Drawing Body", title, NA,
            f"{len(flags)} continuation flag(s), none carry a LINENO field.", ref)
    elif blank:
        add("DXF-05", "Drawing Body", title, REVIEW,
            f"{len(blank)} of {len(with_field)} continuation flag(s) have an "
            "empty line number: "
            + ", ".join(sorted({f.attr('DOCNO') or f.name for f in blank})[:6]),
            ref)
    else:
        add("DXF-05", "Drawing Body", title, PASS,
            f"all {len(with_field)} continuation flag(s) carry a line number.", ref)


def _check_network(model, add, model_width=1191.0, model_height=842.0) -> None:
    """Pipe runs should join up, at a node or through a symbol."""
    from .checks import NA, PASS, REVIEW

    ref = "Rows 21, 28"
    title = "Pipework joins up"
    pipe = [s for s in model.segments if PIPE_LAYERS.match(s.layer) and s.length > 2]
    if len(pipe) < 4:
        add("DXF-06", "Drawing Body", title, NA,
            "Too little process pipe on this sheet to check connectivity.", ref)
        return

    ends = [e for s in pipe for e in s.ends()]
    # Any symbol can break a run, not just the ones we classify -- an
    # unrecognised block is still something the pipe stops at.
    symbols = [(b.x, b.y) for b in model.blocks]

    dangling = []
    for point in ends:
        if sum(1 for other in ends if math.dist(point, other) <= SNAP) > 1:
            continue  # meets another run
        if any(math.dist(point, s) <= SYMBOL_BRIDGE for s in symbols):
            continue  # broken by a symbol, which is normal
        # A run that reaches the sheet edge leaves via a matchline or an
        # off-sheet continuation; that is not a gap.
        if not (EDGE_MARGIN < point[0] < model_width - EDGE_MARGIN
                and EDGE_MARGIN < point[1] < model_height - EDGE_MARGIN):
            continue
        dangling.append(point)

    # INFORMATIONAL ONLY -- deliberately never fails.
    #
    # Measured across the IF439 set, 16-44% of endpoints come back "loose" on
    # every sheet, with no evidence any of those drawings has a real gap. The
    # model is too crude: runs are broken by symbols whose extent we do not
    # know (only an insertion point), by hatches, and by leader lines. Until
    # there is a drawing with a known gap to calibrate against, reporting this
    # as a finding would fire on everything and train people to ignore it.
    #
    # The groundwork is worth keeping: it is what the spec-break-position and
    # reducer-upstream-of-tee checks will need.
    share = len(dangling) / len(ends) if ends else 0.0
    add("DXF-06", "Drawing Body", title, PASS,
        f"{len(ends) - len(dangling)} of {len(ends)} pipe endpoints join up "
        f"({share:.0%} loose). Informational only - connectivity reconstruction "
        "is not yet accurate enough to call a gap.", ref)


def check_continuations(entries) -> None:
    """Cross-sheet: do continuation references resolve, and point back?

    Appends CONT-01 to each report. Checklist rows 20, 21 and 52 -- impossible
    from a PDF, trivial from the DOCNO attribute on the continuation flag.
    """
    from .checks import FAIL, NA, PASS, REVIEW, Result

    checked = [
        (r, s, t) for r, s, t in entries
        if not r.skipped and getattr(s, "dxf", None) is not None
    ]
    if not checked:
        return

    # What this run contains, keyed on the drawing number without its P suffix.
    def base(number: str) -> str:
        return re.sub(r"P\d{2}$", "", normalise_drawing_number(number))

    present = {}
    for report, sheet, tb in checked:
        number = tb.drawing_number or report.drawing_number
        if number:
            present[base(number)] = number

    refs: dict[str, set[str]] = {}
    for report, sheet, tb in checked:
        mine = base(tb.drawing_number or report.drawing_number or report.label)
        refs[mine] = {
            normalise_drawing_number(b.attr("DOCNO"))
            for b in sheet.dxf.of_kind(CONTINUATION)
            if b.attr("DOCNO")
        }

    for report, sheet, tb in checked:
        mine = base(tb.drawing_number or report.drawing_number or report.label)
        targets = sorted(refs.get(mine, ()))
        if not targets:
            report.results.append(Result(
                "CONT-01", "Drawing Body",
                "Continuation references resolve and point back", NA,
                "No continuation references on this sheet.", "Rows 20, 21, 52",
            ))
            continue

        bad_number, one_way, wrong_rev = [], [], []
        for target in targets:
            if not ORIGIN_DWG_NO.fullmatch(target):
                bad_number.append(target)
                continue
            key = base(target)
            if key not in present:
                continue  # simply not in this run; not an error
            # In the set: it should point back at us.
            if mine not in {base(t) for t in refs.get(key, ())}:
                one_way.append(target)
            # ...and at the revision actually in the set.
            actual = present[key]
            if normalise_drawing_number(actual) != target:
                wrong_rev.append(f"{target} (the set holds {actual})")

        problems = []
        if bad_number:
            problems.append(f"malformed drawing number: {', '.join(bad_number)}")
        if wrong_rev:
            problems.append(f"points at a different revision: {'; '.join(wrong_rev)}")
        if one_way:
            problems.append(
                f"{', '.join(one_way)} is in this set but does not reference "
                f"{mine} back"
            )

        in_set = sum(1 for t in targets if base(t) in present)
        if problems:
            report.results.append(Result(
                "CONT-01", "Drawing Body",
                "Continuation references resolve and point back",
                FAIL if (bad_number or wrong_rev) else REVIEW,
                "; ".join(problems)
                + f". {len(targets)} reference(s), {in_set} to sheets in this run.",
                "Rows 20, 21, 52",
            ))
        else:
            report.results.append(Result(
                "CONT-01", "Drawing Body",
                "Continuation references resolve and point back", PASS,
                f"{len(targets)} reference(s), {in_set} of them to sheets in "
                "this run, all reciprocated.",
                "Rows 20, 21, 52",
            ))
