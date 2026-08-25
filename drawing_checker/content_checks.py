"""Checks on the drawing body, the PDF's own metadata, and the batch.

Slice 2.  The frame checks in ``checks.py`` ask "is the drawing labelled
correctly"; these ask "is what is drawn internally consistent", "was this PDF
actually plotted from the revision it claims", and "does this sheet agree with
the rest of the batch".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .extract import Sheet, dedupe_shx_repeat
from .titleblock import ORIGIN_DWG_NO, TitleBlock, normalise_drawing_number

# Line number: <size>-<service>-<sequence>-<spec>-<project>
# e.g. 125-RG-2253-P153-4255
LINE_NUMBER = re.compile(
    r"\b(?P<size>\d{2,4})-(?P<service>[A-Z]{2})-(?P<seq>[A-Z0-9]{3,5})"
    r"-(?P<spec>[A-Z]\d{2,3})-(?P<project>\d{4})\b"
)
# Anything starting like a line number, so partial/placeholder ones are caught
# rather than silently skipped by the strict pattern above.
LINE_NUMBER_START = re.compile(r"\b\d{2,4}-(?:RG|RW|PG|PW|HP|LP)-[A-Z0-9\-]+")
PLACEHOLDER = re.compile(r"^[X?]+$", re.IGNORECASE)

# Crossing tags, per checklist row 26 and the symbology standard
# A-1000-10-DH-009: Pipeline(HPC) / Road(RC) / Water(WC) / HV(HVC) /
# Powerline(PLC) / Comms-Cable(CC).
CROSSING_PREFIXES = {"HPC", "RC", "WC", "HVC", "PLC", "CC"}
CROSSING_TAG = re.compile(r"\b(?P<prefix>[A-Z]{1,3})-(?P<well>[A-Z]{3}\d{3})-\d+(?:-\d+)?\b")
TIE_IN_TAG = re.compile(r"\bTIP-(?P<well>[A-Z]{3}\d{3})-\d+\b")
# Drain / vent tags: bare, or suffixed -M (manual) / -A (automated).
DRAIN_TAG = re.compile(r"\b(?P<kind>LPD|HPV|DIP)(?:-(?P<suffix>[A-Z]+))?\b")
DRAIN_SUFFIXES = {"M", "A"}

# The written crossing-type label drawn beside each crossing tag, and the
# prefix the standard assigns that type (A-1000-10-DH-009 "CROSSING LABELING").
CROSSING_TYPE_LABEL = re.compile(
    r"^(?P<type>WATER|ROAD|PIPELINE|POWERLINE|HV|COMMS|CABLE)\s+CROSSING$"
)
CROSSING_TYPE_PREFIX = {
    "WATER": "WC", "ROAD": "RC", "PIPELINE": "HPC", "POWERLINE": "PLC",
    "HV": "HVC", "COMMS": "CC", "CABLE": "CC",
}

# A drain/vent symbol's unique corridor identifier, per the standard's
# "LPD / HPV LABELING": the corridor-form text drawn against the symbol.
CORRIDOR_ID = re.compile(r"^[A-Z]{3}\d{3}-\d+-\d+$")

# HPV/LPD/DIP text sits touching its identifier text (measured gap 0.0 across
# ARN12 + DP442); anything further belongs to something else.
DRAIN_ID_GAP = 6.0
# Two occurrences of the same tag closer than this are one drawn symbol (the
# SHX capture duplicates strings), not two assets sharing a number.
DISTINCT_SPREAD = 50.0


def _drawn(item) -> bool:
    """A text item that is actually drawn on the sheet.

    The plot driver occasionally parks a degenerate ~1pt annotation at the
    page corner (seen on the issued DP094 set: a second TIP-CMN295-1 at
    (0,841)-(1,842)). It carries real text but marks nothing on the drawing,
    and it made the duplicate checks see phantom same-sheet doubles.
    """
    return (item.x1 - item.x0) >= 2 and (item.y1 - item.y0) >= 2


def _box_gap(a, b) -> float:
    """Rect-to-rect Manhattan gap between two TextItems (0 = touching)."""
    return (max(0.0, max(a.x0, b.x0) - min(a.x1, b.x1))
            + max(0.0, max(a.y0, b.y0) - min(a.y1, b.y1)))

WELL_TAG = re.compile(r"\b([A-Z]{3}\d{3})\b")

# The standard "derived from" wording, per checklist row 22.
DERIVED_FROM = re.compile(
    r"DRAWING AND DATA DERIVED FROM APPROVED\s+(?P<kind>[A-Z]+)\s+DRAWING\s+"
    r"(?P<number>[A-Z]-\d{4}-\d{2}-[A-Z]{2}-[\d/]+(?:P\d{2})?)\s+REV\s+(?P<rev>\S+)",
    re.IGNORECASE,
)
DERIVED_KINDS = {"OPERATIONAL", "PROJECT"}

# AutoCAD's own PDF plot driver. Anything else means the file was re-processed
# after plotting (signed, merged, printed to another driver).
AUTOCAD_PRODUCER = re.compile(r"pdfplot|autocad", re.IGNORECASE)

DATE_TEXT = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")

# How far a plot may pre-date its nominated revision date before it counts as
# a stale plot. One day absorbs both overnight plotting and the fact that PDF
# timestamps are written in inconsistent time zones.
PLOT_GRACE_DAYS = 1


@dataclass
class BatchContext:
    """Cross-drawing information available to a per-sheet check."""

    speller: object | None = None  # spelling.BatchSpeller
    key: str = ""
    maop: object | None = None  # maop.MaopDataset


def parse_frame_date(text: str) -> date | None:
    m = DATE_TEXT.search(text or "")
    if not m:
        return None
    try:
        return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None


def parse_pdf_date(text: str) -> date | None:
    """PDF dates look like ``D:20260721114720+10'00'``."""
    m = re.match(r"D?:?(\d{4})(\d{2})(\d{2})", (text or "").strip())
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def body_items(sheet: Sheet):
    """Everything outside the title block region."""
    return [
        i for i in sheet.items
        if i.cy < 0.85 * sheet.height or i.cx < 0.66 * sheet.width
    ]


def project_code(drawing_number: str) -> str:
    m = re.match(r"^[A-Z]-(\d{4})", drawing_number.upper())
    return m[1] if m else ""


def run_content_checks(sheet: Sheet, tb: TitleBlock, context: BatchContext, add) -> None:
    """``add(code, group, check, status, detail, ref)`` appends a Result."""
    from .checks import FAIL, NA, NO_DATA, PASS, REVIEW  # avoid a circular import

    body = body_items(sheet)
    blob = " ".join(i.text for i in body)

    # -- line numbers ---------------------------------------------------
    # Track where each candidate is drawn: a malformed line number cannot be
    # found by the markup pass's well-formed patterns, so LN-01 must carry
    # its own anchors.
    candidate_rects: dict[str, list] = {}
    for item in body:
        for candidate in LINE_NUMBER_START.findall(item.text):
            candidate_rects.setdefault(candidate, []).append(
                (item.x0, item.y0, item.x1, item.y1)
            )
    candidates = set(candidate_rects)
    valid, placeholders, malformed = set(), set(), set()
    for candidate in candidates:
        m = LINE_NUMBER.fullmatch(candidate)
        if not m:
            malformed.add(candidate)
        elif PLACEHOLDER.match(m["seq"]):
            placeholders.add(candidate)
        else:
            valid.add(candidate)

    if not candidates:
        add("LN-01", "Drawing Body", "Line numbers present and fully assigned",
            NA, "No line numbers on this sheet (key plans carry none).",
            "Rows 24, 54")
        add("LN-02", "Drawing Body", "Line number project codes match the drawing",
            NA, "No line numbers to check.", "Rows 24, 54")
    else:
        # An XXXX sequence is a deliberate hold, not a defect -- the sequence is
        # assigned later from the MAOP calc -- so placeholders are counted and
        # reported but never fail. Only a malformed number is wrong.
        problems = []
        if malformed:
            problems.append(f"malformed: {', '.join(sorted(malformed))}")
        # Worded as its own sentence: "malformed: X 3 on an XXXX hold" read as
        # if the malformed number were the hold (Jordan's combined.pdf
        # comment).
        held = (f" Separately, {len(placeholders)} other line number(s) are "
                "on an XXXX sequence hold (not a defect)."
                if placeholders else "")
        add("LN-01", "Drawing Body", "Line numbers present and fully assigned",
            FAIL if problems else PASS,
            "; ".join(problems) + "." + held if problems
            else f"{len(valid) + len(placeholders)} line number(s), all well "
                 f"formed.{held}",
            "Rows 24, 54",
            anchors=[r for c in sorted(malformed) for r in candidate_rects[c]]
            if malformed else None)

        expected = project_code(tb.drawing_number)
        if not expected:
            add("LN-02", "Drawing Body", "Line number project codes match the drawing",
                NO_DATA, "Drawing number not parseable.", "Rows 24, 54")
        else:
            wrong = sorted(
                ln for ln in valid | placeholders
                if (m := LINE_NUMBER.fullmatch(ln)) and m["project"] != expected
            )
            add("LN-02", "Drawing Body", "Line number project codes match the drawing",
                REVIEW if wrong else PASS,
                f"drawing is project {expected}; line numbers from another "
                f"project: {', '.join(wrong)}" if wrong
                else f"all line numbers carry project {expected}.",
                "Rows 24, 54")

    # -- crossing / tie-in / drain tags ----------------------------------
    bad_prefix = sorted({
        m.group(0) for m in CROSSING_TAG.finditer(blob)
        if m["prefix"] not in CROSSING_PREFIXES and m["prefix"] != "TIP"
    })
    bad_suffix = sorted({
        m.group(0) for m in DRAIN_TAG.finditer(blob)
        if m["suffix"] and len(m["suffix"]) <= 2 and m["suffix"] not in DRAIN_SUFFIXES
    })
    tag_count = len(CROSSING_TAG.findall(blob)) + len(TIE_IN_TAG.findall(blob))
    if not tag_count and not bad_suffix:
        add("TAG-01", "Drawing Body", "Crossing, tie-in and drain tags well formed",
            NA, "No crossing, tie-in or drain tags on this sheet.", "Rows 26, 29, 61")
    else:
        problems = []
        if bad_prefix:
            problems.append(
                f"crossing prefix not in {sorted(CROSSING_PREFIXES)}: "
                f"{', '.join(bad_prefix)}"
            )
        if bad_suffix:
            problems.append(f"drain tag suffix not M or A: {', '.join(bad_suffix)}")
        # A prefix we simply do not know is a gap in the allowed list, not
        # proof of an error, so it only earns a REVIEW.
        add("TAG-01", "Drawing Body", "Crossing, tie-in and drain tags well formed",
            FAIL if bad_suffix else (REVIEW if bad_prefix else PASS),
            "; ".join(problems) or f"{tag_count} tag(s), all well formed.",
            "Rows 26, 29, 61")

    # Tie-in and crossing tags must name a wellsite that is actually drawn.
    drawn = set(WELL_TAG.findall(blob))
    orphans = sorted({
        m.group(0)
        for pattern in (CROSSING_TAG, TIE_IN_TAG)
        for m in pattern.finditer(blob)
        if m["well"] not in drawn
    })
    add("TAG-02", "Drawing Body", "Tags reference a wellsite drawn on the sheet",
        NA if not tag_count else (REVIEW if orphans else PASS),
        "No tags to check." if not tag_count
        else (f"tag(s) naming a wellsite not otherwise on the sheet: "
              f"{', '.join(orphans)}" if orphans
              else f"all tags reference one of: {', '.join(sorted(drawn))}."),
        "Rows 26, 29, 61")

    # A crossing's written type label must agree with its tag prefix, per the
    # A-1000-10-DH-009 legend -- "POWERLINE CROSSING" beside a WC-... tag
    # means one of the two is wrong. The label touches its tag (gap 0.0
    # measured across ARN12 + DP442), so the association is not a guess.
    labels = [i for i in body if CROSSING_TYPE_LABEL.match(i.text.strip())]
    tag_items = [i for i in body if CROSSING_TAG.fullmatch(i.text.strip())]
    if not labels:
        add("TAG-04", "Drawing Body", "Crossing tag prefix matches the crossing type",
            NA, "No crossing type labels on this sheet.", "Rows 26, 61")
    else:
        wrong, checked_labels = [], 0
        wrong_anchors = []
        for label in labels:
            nearest = min(tag_items, key=lambda t: _box_gap(label, t), default=None)
            if nearest is None or _box_gap(label, nearest) > 12:
                continue  # a legend entry, not a placed crossing
            checked_labels += 1
            kind = CROSSING_TYPE_LABEL.match(label.text.strip())["type"]
            expected = CROSSING_TYPE_PREFIX[kind]
            tag = nearest.text.strip()
            if not tag.startswith(expected + "-"):
                wrong.append(
                    f"{tag} is labelled '{label.text.strip()}' - a "
                    f"{kind.lower()} crossing takes the {expected} prefix"
                )
                wrong_anchors.append((nearest.x0, nearest.y0, nearest.x1, nearest.y1))
        add("TAG-04", "Drawing Body", "Crossing tag prefix matches the crossing type",
            FAIL if wrong else (PASS if checked_labels else NA),
            "; ".join(wrong) if wrong
            else (f"{checked_labels} crossing(s), each tag prefix matching its "
                  "written type." if checked_labels
                  else "No crossing type labels beside a crossing tag."),
            "Rows 26, 61", anchors=wrong_anchors or None)

    # Jordan (2026-08-20): OD560 pipe is hard to purchase -- flag it and
    # recommend sizing up to OD630.
    sized_560: list[str] = []
    anchors_560 = []
    for candidate, rects in candidate_rects.items():
        m = LINE_NUMBER.fullmatch(candidate)
        if m and m["size"] == "560":
            sized_560.append(candidate)
            anchors_560.extend(rects)
    for item in body:
        for m in re.finditer(r"\b(?:R[GW])?560V[A-Z]\d{2}\b", item.text):
            sized_560.append(m.group(0))
            anchors_560.append((item.x0, item.y0, item.x1, item.y1))
    if sized_560:
        add("SIZE-01", "Piping Spec", "No OD560 piping (size up to OD630)",
            REVIEW,
            "OD560 is drawn - recommend sizing up to OD630 for ease of "
            "purchasing: " + ", ".join(sorted(set(sized_560))),
            "Rows 24, 54", anchors=anchors_560)
    else:
        add("SIZE-01", "Piping Spec", "No OD560 piping (size up to OD630)",
            NA if not candidates else PASS,
            "No line numbers on this sheet." if not candidates
            else "No OD560 pipe or valves drawn.", "Rows 24, 54")

    # -- "derived from" wording -----------------------------------------
    if not tb.derived_from:
        add("TB-19", "Title Block", "'Derived from' note uses the standard wording",
            NA, "No 'derived from' note on this drawing.", "Rows 22, 48")
    else:
        m = DERIVED_FROM.search(tb.derived_from)
        if not m:
            add("TB-19", "Title Block", "'Derived from' note uses the standard wording",
                FAIL,
                "The note does not match the required wording 'DRAWING AND DATA "
                "DERIVED FROM APPROVED OPERATIONAL/PROJECT DRAWING <number> REV "
                f"<rev>': {tb.derived_from[:150]}", "Rows 22, 48")
        elif m["kind"].upper() not in DERIVED_KINDS:
            add("TB-19", "Title Block", "'Derived from' note uses the standard wording",
                FAIL,
                f"The note says 'APPROVED {m['kind'].upper()} DRAWING' - it must "
                f"be OPERATIONAL (for an as-built source) or PROJECT. Full note: "
                f"{tb.derived_from[:150]}", "Rows 22, 48")
        elif not ORIGIN_DWG_NO.fullmatch(
                normalise_drawing_number(tb.drawing_number or "")):
            # Covers both an empty box and positional-fallback garbage
            # ('NTS' on a plot without the title-block xref).
            add("TB-19", "Title Block", "'Derived from' note uses the standard wording",
                NO_DATA,
                "The note is well formed but this drawing's own number could "
                "not be read, so the numbers cannot be compared.",
                "Rows 22, 48")
        else:
            # Jordan (2026-08-20): the number in the note must be THIS
            # drawing's number, minus the project modifier (P01 etc.). A
            # startswith comparison was too loose -- ...-017 would have
            # accepted ...-0179.
            source = normalise_drawing_number(m["number"])
            base = re.sub(r"P\d{2}$", "",
                          normalise_drawing_number(tb.drawing_number))
            matches = re.sub(r"P\d{2}$", "", source) == base
            add("TB-19", "Title Block", "'Derived from' note uses the standard wording",
                PASS if matches else FAIL,
                f"derived from {source} rev {m['rev']}"
                + ("" if matches else
                   f" - that is not this drawing ({tb.drawing_number} minus "
                   "its modifier). The note must name the drawing it sits on."),
                "Rows 22, 48")

    # -- title block signatures belong to the Rev 0 issue -----------------
    rev0 = next((r for r in tb.revision_history if r.revision == "0"), None)
    signed = [s for s in tb.signoffs if s.date.strip()]
    if not rev0:
        add("TB-18", "Title Block", "Title block signatures match the Rev 0 issue",
            NA if not signed else REVIEW,
            "No Rev 0 row in the revision history."
            + ("" if not signed else " The title block is nonetheless signed - "
               f"dates {sorted({s.date for s in signed})}."),
            "Rows 18, 44")
    elif not signed:
        add("TB-18", "Title Block", "Title block signatures match the Rev 0 issue",
            FAIL,
            f"Rev 0 was issued on {rev0.date} but the title block signature "
            "boxes are empty.", "Rows 18, 44")
    else:
        dates = {s.date.strip() for s in signed}
        rev0_date = rev0.date.strip()
        if dates == {rev0_date}:
            add("TB-18", "Title Block", "Title block signatures match the Rev 0 issue",
                PASS, f"all six boxes dated {rev0_date}, matching the Rev 0 row.",
                "Rows 18, 44")
        else:
            add("TB-18", "Title Block", "Title block signatures match the Rev 0 issue",
                REVIEW,
                f"Rev 0 row is dated {rev0_date} but the title block carries "
                f"{sorted(dates)}. The signature block records the Rev 0 issue, "
                "so these should agree.", "Rows 18, 44")

    # -- plot metadata ----------------------------------------------------
    plotted = parse_pdf_date(sheet.meta.get("creationDate", ""))
    latest = tb.revision_history[0] if tb.revision_history else None
    rev_date = parse_frame_date(latest.date) if latest else None
    if not plotted or not rev_date:
        add("PLT-01", "Drawing File", "PDF plotted no earlier than its revision",
            NO_DATA,
            f"plot date={plotted or 'unreadable'}, revision date="
            f"{rev_date or 'unreadable'}.", "Row 82")
    elif (rev_date - plotted).days > PLOT_GRACE_DAYS:
        add("PLT-01", "Drawing File", "PDF plotted no earlier than its revision",
            FAIL,
            f"This PDF was plotted on {plotted:%d/%m/%Y}, "
            f"{(rev_date - plotted).days} days before rev {latest.revision} is "
            f"dated ({rev_date:%d/%m/%Y}). The PDF pre-dates the revision it "
            "shows, so it is a stale plot - re-issue from the current DWG.",
            "Row 82")
    elif plotted < rev_date:
        # Plotting the evening before the nominated issue date is normal, and
        # PDF timestamps are recorded in inconsistent time zones.
        add("PLT-01", "Drawing File", "PDF plotted no earlier than its revision",
            PASS,
            f"plotted {plotted:%d/%m/%Y}, the day before the rev "
            f"{latest.revision} date of {rev_date:%d/%m/%Y}.", "Row 82")
    else:
        gap = (plotted - rev_date).days
        add("PLT-01", "Drawing File", "PDF plotted no earlier than its revision",
            PASS if gap <= 90 else REVIEW,
            f"plotted {plotted:%d/%m/%Y}, rev {latest.revision} dated "
            f"{rev_date:%d/%m/%Y}"
            + (f" - {gap} days apart, confirm this is the current plot."
               if gap > 90 else "."),
            "Row 82")

    producer = sheet.meta.get("producer", "") or ""
    creator = sheet.meta.get("creator", "") or ""
    if not producer and not creator:
        add("PLT-02", "Drawing File", "PDF came straight from the AutoCAD plotter",
            NO_DATA, "No producer recorded in the PDF.", "Row 82")
    elif AUTOCAD_PRODUCER.search(producer):
        add("PLT-02", "Drawing File", "PDF came straight from the AutoCAD plotter",
            PASS, f"{producer} / {creator}", "Row 82")
    else:
        add("PLT-02", "Drawing File", "PDF came straight from the AutoCAD plotter",
            REVIEW,
            f"Produced by '{producer}' (created by '{creator}'), not AutoCAD's "
            "plot driver. The file has been re-processed after plotting - "
            "confirm it is the drawing you mean to issue and that nothing was "
            "flattened or altered.", "Row 82")

    # -- spelling ---------------------------------------------------------
    speller = context.speller
    if speller is None:
        add("SP-01", "Notes & Titles", "Spell check of notes and titles", NO_DATA,
            "No batch speller available.", "Rows 35, 49")
    else:
        misspellings = speller.check(context.key)
        # Box the suspect words on the drawing (Jordan's combined.pdf
        # comment): the markup locator only matches tag-shaped tokens, so the
        # words need explicit anchors.
        word_anchors = []
        for m in misspellings:
            pattern = re.compile(rf"\b{re.escape(m.word)}\b", re.IGNORECASE)
            for item in sheet.items:
                if item.source in ("shx", "dxf") and pattern.search(item.text):
                    word_anchors.append((item.x0, item.y0, item.x1, item.y1))
        add("SP-01", "Notes & Titles", "Spell check of notes and titles",
            REVIEW if misspellings else PASS,
            "; ".join(str(m) for m in misspellings) if misspellings
            else "No suspect words anywhere on the drawing.",
            "Rows 35, 49",
            anchors=word_anchors[:12] or None)


def _one_edit_or_swap(a: str, b: str) -> bool:
    """Single substitution, insertion, deletion, or adjacent transposition."""
    if a == b:
        return False
    if len(a) == len(b):
        diffs = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        if len(diffs) == 1:
            return True
        if len(diffs) == 2 and diffs[1] == diffs[0] + 1:
            i, j = diffs
            return a[i] == b[j] and a[j] == b[i]  # transposition, e.g. PHS/PSH
        return False
    if abs(len(a) - len(b)) > 1:
        return False
    short, long = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(long)):
        if long[:i] + long[i + 1:] == short:
            return True
    return False


def check_wellsite_prefixes(entries) -> None:
    """Catch mistyped wellsite prefixes, e.g. PSH059 for PHS059.

    Two signals, both drawn from the batch as a whole:

    1. The same wellsite number appearing under two different prefixes. One of
       them is wrong -- on this batch that caught PSH059/PHS059 on a sheet that
       carries both spellings.
    2. A prefix used on only one drawing that is a single edit (including a
       transposition) away from a prefix used across the batch.

    A genuinely different field code, such as CNH001 sitting among CMN tags, is
    two edits away and correctly left alone.
    """
    from .checks import FAIL, PASS, REVIEW, Result

    checked = [(r, s, t) for r, s, t in entries if not r.skipped]
    if not checked:
        return

    prefix_drawings: dict[str, set[str]] = {}
    number_prefixes: dict[str, set[str]] = {}
    per_sheet: dict[str, set[str]] = {}
    for report, sheet, _ in checked:
        tags = set(WELL_TAG.findall(" ".join(i.text for i in body_items(sheet))))
        per_sheet[report.label] = tags
        for tag in tags:
            prefix, number = tag[:3], tag[3:]
            prefix_drawings.setdefault(prefix, set()).add(report.label)
            number_prefixes.setdefault(number, set()).add(prefix)

    common = {p for p, files in prefix_drawings.items() if len(files) >= 2}

    def usage(prefix: str) -> int:
        return len(prefix_drawings.get(prefix, ()))

    for report, _, _ in checked:
        clashes, near = [], []
        for tag in sorted(per_sheet[report.label]):
            prefix, number = tag[:3], tag[3:]
            # Different fields legitimately reuse well numbers (COM007 and
            # CNH007 are different wells). Only a near-identical prefix means
            # one of them is mistyped.
            rivals = {
                r for r in number_prefixes.get(number, set()) - {prefix}
                if _one_edit_or_swap(prefix, r)
            }
            if rivals:
                # Blame only the sheet carrying the rarer spelling -- a sheet
                # that spells the wellsite correctly is not at fault for a
                # typo made on another drawing.
                best = max(rivals | {prefix}, key=usage)
                if prefix != best:
                    clashes.append(f"{tag} - elsewhere in the batch it is {best}{number}")
            elif prefix not in common:
                nearby = [c for c in common if _one_edit_or_swap(prefix, c)]
                if nearby:
                    near.append(f"{tag} (prefix {prefix} vs {'/'.join(sorted(nearby))})")

        if clashes:
            status, detail = FAIL, (
                "Wellsite tag mistyped - the same wellsite number is spelled "
                "differently on other drawings in this batch: " + "; ".join(clashes)
            )
        elif near:
            status, detail = REVIEW, (
                "Wellsite prefix used nowhere else in the batch and one letter "
                "from a common one: " + "; ".join(near)
            )
        else:
            status, detail = PASS, (
                f"{len(per_sheet[report.label])} wellsite tag(s), prefixes "
                "consistent with the batch."
            )
        report.results.append(Result(
            "WELL-01", "Drawing Body", "Wellsite tags spelled consistently",
            status, detail, "Rows 26, 30",
        ))


def _levenshtein(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 2:
        return 99
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(
                previous[j] + 1,        # deletion
                current[j - 1] + 1,     # insertion
                previous[j - 1] + (ca != cb),  # substitution
            ))
        previous = current
    return previous[-1]


def continuation_refs(sheet: Sheet) -> list:
    """Drawing numbers on the continuation flags around the sheet border.

    A continuation flag is the pennant a line runs into where it leaves the
    sheet; its text is a bare drawing number and it butts against the frame.
    Measured across the DP094 set the flags sit in tight bands -- left
    cx/w ~0.074, right ~0.935, top cy/h ~0.097, bottom ~0.807 -- while the
    standard-detail callouts scattered through the body (Q-1300-10-DP-0214)
    never reach them. The nearest body callout to a band is 5pt outside it, so
    the bands are kept tight on purpose; widening them picks up detail
    references and the check goes noisy.

    Anything below the bottom band is frame furniture (the revision and
    reference-drawing tables), not a continuation.
    """
    width, height = sheet.width, sheet.height
    found = []
    for item in sheet.items:
        text = item.text.strip()
        if not ORIGIN_DWG_NO.fullmatch(text):
            continue
        if item.cy > 0.885 * height:
            continue  # revision / reference-drawing tables
        at_edge = (
            item.cx < 0.084 * width          # left border
            or item.cx > 0.924 * width       # right border
            or item.cy < 0.107 * height      # top border
            or 0.79 * height < item.cy < 0.825 * height  # bottom border
        )
        if at_edge:
            found.append((text, item))
    return found


def _sheet_number(report, tb) -> str:
    """A sheet's drawing number: the title block, else the filename.

    The filename fallback matters for a frame the tool cannot read (a re-plot
    without the title-block xref): the sheet is still IN the set, and treating
    it as absent made CONT-02 claim its neighbours' references were broken
    (Jordan's combined.pdf comments, 2026-08-20). A title-block value that is
    not a plausible Origin number ('NTS', grabbed positionally from a
    frame-less plot) is treated as absent, and any Origin-shaped number in
    the filename counts, whatever the surrounding naming.
    """
    from .checks import parse_filename

    number = tb.drawing_number or report.drawing_number
    if number and not ORIGIN_DWG_NO.fullmatch(normalise_drawing_number(number)):
        number = ""
    if not number:
        number = parse_filename(Path(report.filename)).drawing_number
    if not number:
        m = ORIGIN_DWG_NO.search(Path(report.filename).stem.upper())
        number = m.group(0) if m else ""
    return normalise_drawing_number(number or "")


def check_continuation_targets(entries) -> None:
    """Continuation flags naming a drawing in this set must name it exactly.

    Checklist rows 20, 21, reshaped per Jordan's 2026-08-20 comments:

    - A target whose base number is in the set but whose modifier differs
      (``...-178`` where the set issues ``...-178P01``) is the mistake this
      check exists for -- FAIL.
    - A target one character from an in-set drawing is a typo -- FAIL.
    - A target genuinely outside the set is NOT a finding: lines legitimately
      continue into other packages, and flagging them was noise.
    - Only DF (PEFS) document numbers are continuations at all; the
      Q-1300-10-DP standard-detail callouts that stray into the border bands
      are ignored.
    """
    from .checks import FAIL, NA, PASS, Result

    checked = [(r, s, t) for r, s, t in entries if not r.skipped]
    if not checked:
        return

    def base(number: str) -> str:
        return re.sub(r"P\d{2}$", "", number)

    numbers = [_sheet_number(r, t) for r, _, t in checked]
    present = {n for n in numbers if n}
    issued = {base(n): n for n in present}

    def doc_type(number: str) -> str:
        m = re.match(r"^[A-Z]-\d{4}-\d{2}-([A-Z]{2})-", number)
        return m[1] if m else ""

    for (report, sheet, tb), mine in zip(checked, numbers):
        refs = continuation_refs(sheet)
        targets = sorted({
            normalise_drawing_number(text) for text, _ in refs
            if doc_type(normalise_drawing_number(text)) == "DF"
        } - {mine})

        if not targets:
            report.results.append(Result(
                "CONT-02", "Drawing Body",
                "Continuation references name a drawing in the set", NA,
                "No continuation flags on this sheet.", "Rows 20, 21",
            ))
            continue

        anchors = {
            normalise_drawing_number(text): (item.x0, item.y0, item.x1, item.y1)
            for text, item in refs
        }
        problems, bad_targets = [], []
        outside = 0
        for target in targets:
            if target in present:
                continue
            if base(target) in issued and base(target) != base(mine):
                problems.append(
                    f"{target} names a drawing in this set without its "
                    f"current project modifier - the set issues "
                    f"{issued[base(target)]}"
                )
                bad_targets.append(target)
                continue
            # Not `mine`: a sheet is one character from its own neighbours in
            # a sequentially numbered set, and "you meant this drawing itself"
            # is never the right advice. And only a LENGTH-CHANGING edit (a
            # dropped or doubled digit, DF-22008 for DF-220008) counts as a
            # typo: in a sequentially numbered field a same-length
            # substitution (DF-083 beside in-set DF-081/085/093) is almost
            # always a real neighbouring sheet in another package -- measured
            # on the 46-drawing all-projects batch, every same-length hit was
            # one.
            near = sorted(
                p for p in present
                if p != mine and len(p) != len(target)
                and _levenshtein(target, p) <= 1
            )
            if near:
                problems.append(
                    f"{target} is not in the set but is one character from "
                    f"{', '.join(near)} - a typo"
                )
                bad_targets.append(target)
            else:
                outside += 1  # continues into another package: not a finding

        resolved = sum(1 for t in targets if t in present)
        tail = (f" {resolved} of {len(targets)} reference(s) resolve within "
                f"the set"
                + (f"; {outside} continue into other packages (not checked)."
                   if outside else "."))
        if problems:
            report.results.append(Result(
                "CONT-02", "Drawing Body",
                "Continuation references name a drawing in the set", FAIL,
                "; ".join(problems) + "." + tail, "Rows 20, 21",
                anchors=[anchors[t] for t in bad_targets if t in anchors],
            ))
        else:
            report.results.append(Result(
                "CONT-02", "Drawing Body",
                "Continuation references name a drawing in the set", PASS,
                tail.strip(), "Rows 20, 21",
            ))


def check_tie_in_duplicates(entries) -> None:
    """TIP-01: has the same tie-in point number been used twice in the project?

    Checklist rows 26, 29, 61. Text alone cannot tell a doubled-up number from
    the same physical tie-in legitimately drawn on two adjacent sheets (the way
    TIP-CMN296-1 appears on two DP094 drawings), so a repeat is REVIEW, never
    FAIL -- but measured across the ARN05/ARN10/ARN12 sets it fires on 1 tag in
    119, so it stays quiet on a clean batch.
    """
    from .checks import NA, PASS, REVIEW, Result

    checked = [(r, s, t) for r, s, t in entries if not r.skipped]
    if not checked:
        return

    # tag -> {label: [rects]}
    occurrences: dict[str, dict[str, list]] = {}
    for report, sheet, _ in checked:
        for item in body_items(sheet):
            if not _drawn(item):
                continue
            for m in TIE_IN_TAG.finditer(item.text):
                occurrences.setdefault(m.group(0), {}).setdefault(
                    report.label, []
                ).append((item.x0, item.y0, item.x1, item.y1))

    for report, sheet, _ in checked:
        mine = {t: v for t, v in occurrences.items() if report.label in v}
        if not mine:
            report.results.append(Result(
                "TIP-01", "Drawing Body",
                "Tie-in point numbers unique within the batch", NA,
                "No tie-in point tags on this sheet.", "Rows 26, 29, 61",
            ))
            continue

        repeats, anchors = [], []
        for tag, by_sheet in sorted(mine.items()):
            others = sorted(label for label in by_sheet if label != report.label)
            if others:
                repeats.append(
                    f"{tag} also appears on: {', '.join(o[:48] for o in others)}"
                )
                anchors.extend(by_sheet[report.label])
            else:
                # Twice on this sheet, at genuinely different places. Two
                # rects for one drawn tag (the SHX capture, a leader) sit
                # nearly on top of each other, so distance separates them.
                rects = by_sheet[report.label]
                spread = max(
                    abs(a[0] - b[0]) + abs(a[1] - b[1])
                    for a in rects for b in rects
                )
                if len(rects) > 1 and spread > 50:
                    repeats.append(
                        f"{tag} is drawn {len(rects)} times on this sheet"
                    )
                    anchors.extend(rects)

        if repeats:
            report.results.append(Result(
                "TIP-01", "Drawing Body",
                "Tie-in point numbers unique within the batch", REVIEW,
                "Tie-in number used more than once - confirm it is the same "
                "physical tie-in drawn on adjacent sheets, not two points "
                "sharing a number: " + "; ".join(repeats),
                "Rows 26, 29, 61", anchors=anchors,
            ))
        else:
            report.results.append(Result(
                "TIP-01", "Drawing Body",
                "Tie-in point numbers unique within the batch", PASS,
                f"{len(mine)} tie-in tag(s), none repeated elsewhere in the "
                "batch.", "Rows 26, 29, 61",
            ))


def check_reference_modifiers(entries) -> None:
    """REF-01: reference-drawing entries naming a sheet in this set must use
    that sheet's current number, project modifier included.

    Jordan (2026-08-20): a project drawing issued as Q-...-0099P01 is often
    listed in other sheets' reference tables as the bare Q-...-0099 it was
    derived from -- the modifier gets forgotten. Same base number but a
    different modifier than the set issues is a FAIL, matching CONT-01's
    wrong-revision rule.
    """
    from .checks import FAIL, NA, PASS, Result

    checked = [(r, s, t) for r, s, t in entries if not r.skipped]
    if not checked:
        return

    def base(number: str) -> str:
        return re.sub(r"P\d{2}$", "", normalise_drawing_number(number))

    issued = {}
    for report, _, tb in checked:
        number = tb.drawing_number or report.drawing_number
        if number:
            issued[base(number)] = normalise_drawing_number(number)

    for report, _, tb in checked:
        mine = base(tb.drawing_number or report.drawing_number or "")
        refs = [normalise_drawing_number(n) for n, _ in tb.reference_drawings if n]
        if not refs:
            report.results.append(Result(
                "REF-01", "Title Block",
                "Reference drawings use the in-set drawing numbers", NA,
                "No reference drawings table read.", "Rows 19, 47",
            ))
            continue
        stale = []
        in_set = 0
        for ref in refs:
            key = base(ref)
            if key not in issued or key == mine:
                continue
            in_set += 1
            if ref != issued[key]:
                stale.append(f"{ref} (this set issues {issued[key]})")
        if stale:
            report.results.append(Result(
                "REF-01", "Title Block",
                "Reference drawings use the in-set drawing numbers", FAIL,
                "Reference drawings table names a drawing in this set without "
                "its current project modifier: " + "; ".join(sorted(set(stale))),
                "Rows 19, 47",
            ))
        else:
            report.results.append(Result(
                "REF-01", "Title Block",
                "Reference drawings use the in-set drawing numbers", PASS,
                f"{in_set} reference(s) to sheets in this set, all carrying "
                "the issued number." if in_set
                else "No reference points at another sheet in this set.",
                "Rows 19, 47",
            ))


def check_asset_tag_duplicates(entries) -> None:
    """TAG-03: HPV / LPD / DIP identifiers and crossing tags used once only.

    Jordan (2026-08-20): doubled-up HPV/LPD numbers have been slipping
    through. A drain symbol's identifier is the corridor-form text touching it
    (gap 0.0 measured across ARN12 + DP442); uniqueness is per kind -- an HPV
    and an LPD legitimately share a corridor identifier, two HPVs do not.
    Crossing tags are globally unique already. REVIEW, not FAIL, for the same
    reason as TIP-01: the same physical asset can be drawn on two adjacent
    sheets.
    """
    from .checks import NA, PASS, REVIEW, Result

    checked = [(r, s, t) for r, s, t in entries if not r.skipped]
    if not checked:
        return

    # (kind, identifier) -> {label: [rect,...]}   e.g. ("HPV", "CMN399-2-1")
    seen: dict[tuple[str, str], dict[str, list]] = {}
    counts: dict[str, int] = {}
    for report, sheet, _ in checked:
        body = [i for i in body_items(sheet) if _drawn(i)]
        ids = [i for i in body if CORRIDOR_ID.match(i.text.strip())]
        n = 0
        for item in body:
            text = item.text.strip()
            rect = (item.x0, item.y0, item.x1, item.y1)
            m = CROSSING_TAG.fullmatch(text)
            if m and m["prefix"] in CROSSING_PREFIXES:
                # TIP- tags match the same shape but belong to TIP-01.
                key = ("crossing", text)
            elif re.fullmatch(r"(?:HPV|LPD|DIP)(?:-[MA])?", text):
                ident = min(ids, key=lambda i: _box_gap(item, i), default=None)
                if ident is None or _box_gap(item, ident) > DRAIN_ID_GAP:
                    continue  # a note mention, not a placed symbol
                key = (text.split("-")[0], ident.text.strip())
            else:
                continue
            n += 1
            seen.setdefault(key, {}).setdefault(report.label, []).append(rect)
        counts[report.label] = n

    def distinct(rects) -> bool:
        return max(
            abs(a[0] - b[0]) + abs(a[1] - b[1]) for a in rects for b in rects
        ) > DISTINCT_SPREAD

    for report, sheet, _ in checked:
        if not counts.get(report.label):
            report.results.append(Result(
                "TAG-03", "Drawing Body",
                "HPV / LPD / crossing identifiers unique within the batch", NA,
                "No drain or crossing identifiers on this sheet.",
                "Rows 26, 29, 61",
            ))
            continue
        repeats, anchors = [], []
        for (kind, ident), by_sheet in sorted(seen.items()):
            if report.label not in by_sheet:
                continue
            name = ident if kind == "crossing" else f"{kind} {ident}"
            others = sorted(l for l in by_sheet if l != report.label)
            mine = by_sheet[report.label]
            if others:
                repeats.append(
                    f"{name} also appears on: {', '.join(o[:48] for o in others)}"
                )
                anchors.extend(mine)
            elif len(mine) > 1 and distinct(mine):
                repeats.append(f"{name} is drawn {len(mine)} times on this sheet")
                anchors.extend(mine)
        if repeats:
            report.results.append(Result(
                "TAG-03", "Drawing Body",
                "HPV / LPD / crossing identifiers unique within the batch",
                REVIEW,
                "Identifier used more than once - confirm it is one asset "
                "drawn on adjacent sheets, not two assets sharing a number: "
                + "; ".join(repeats), "Rows 26, 29, 61", anchors=anchors,
            ))
        else:
            report.results.append(Result(
                "TAG-03", "Drawing Body",
                "HPV / LPD / crossing identifiers unique within the batch",
                PASS,
                f"{counts[report.label]} identifier(s), none repeated.",
                "Rows 26, 29, 61",
            ))


def run_batch_checks(entries) -> None:
    """Cross-drawing checks. ``entries`` is a list of (SheetReport, Sheet, TitleBlock).

    Appends results to each report in place.
    """
    from .checks import PASS, REVIEW, Result

    checked = [
        (report, sheet, tb) for report, sheet, tb in entries if not report.skipped
    ]
    if not checked:
        return

    check_wellsite_prefixes(entries)

    from .notes import check_note_consistency
    check_note_consistency(entries)

    from .dxf_checks import check_continuations
    check_continuations(entries)

    check_continuation_targets(entries)

    from .flow_checks import check_flow_direction
    check_flow_direction(entries)

    check_tie_in_duplicates(entries)
    check_asset_tag_duplicates(entries)
    check_reference_modifiers(entries)

    # Duplicate drawing numbers in one issue are always a mistake. Only
    # plausible Origin numbers are compared -- positional-fallback garbage
    # ('NTS' from a frame-less plot) matching itself across sheets is noise.
    seen: dict[str, list[str]] = {}
    for report, _, _ in checked:
        number = normalise_drawing_number(report.drawing_number or "")
        if number and ORIGIN_DWG_NO.fullmatch(number):
            seen.setdefault(report.drawing_number.upper(), []).append(report.label)

    # The batch's normal sheet size and producer, for spotting the odd one out.
    def majority(values):
        counts: dict[str, int] = {}
        for v in values:
            counts[v] = counts.get(v, 0) + 1
        return max(counts, key=counts.get) if counts else ""

    common_size = majority([f"{s.width:.0f}x{s.height:.0f}" for _, s, _ in checked])
    common_producer = majority([(s.meta.get("producer") or "") for _, s, _ in checked])

    for report, sheet, _ in checked:
        others = [
            label for label in seen.get(report.drawing_number.upper(), [])
            if label != report.label
        ]
        report.results.append(Result(
            "BAT-01", "Drawing File", "Drawing number unique within the batch",
            REVIEW if others else PASS,
            f"{report.drawing_number} also appears in: {', '.join(others)}"
            if others else f"{report.drawing_number} appears once.",
            "Rows 11, 81",
        ))

        size = f"{sheet.width:.0f}x{sheet.height:.0f}"
        producer = sheet.meta.get("producer") or ""
        odd = []
        if len(checked) > 2 and size != common_size:
            odd.append(f"sheet size {size} against {common_size} for the batch")
        if len(checked) > 2 and producer != common_producer:
            odd.append(f"produced by '{producer}' against '{common_producer}'")
        report.results.append(Result(
            "BAT-02", "Drawing File", "Sheet consistent with the rest of the batch",
            REVIEW if odd else PASS,
            "; ".join(odd) or f"{size}, {producer or 'no producer'} - matches the batch.",
            "Row 81",
        ))

    _cap_ocr_findings(entries)


def _cap_ocr_findings(entries) -> None:
    """Batch checks append Results directly, so they miss the per-sheet cap.

    Same rule as in ``run_checks``: nothing read from an OCR text layer may
    assert a defect outright, because a misread character is indistinguishable
    from a drafting error.
    """
    from .checks import FAIL, REVIEW

    for report, sheet, _ in entries:
        if not getattr(sheet, "is_ocr", False):
            continue
        for result in report.results:
            if result.status == FAIL:
                result.status = REVIEW
                result.detail = (
                    f"{result.detail} [Read from an OCR text layer, so this may "
                    "be a misread rather than a drawing error - confirm against "
                    "the drawing.]"
                )
