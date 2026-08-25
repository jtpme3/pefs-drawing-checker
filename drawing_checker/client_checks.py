"""Checks taken from client comments on the DP442 IFR checkprint.

Source: ``reference/ARN10 Project Documents/DP442 IFR Checkprint ECT Check.pdf``
(Bluebeam, reviewers KC / RAT / Ewan Thiele). Only the comments that can be
decided objectively from the drawing text are implemented; the rest are listed
at the bottom of this module so it is clear what still needs a human.
"""

from __future__ import annotations

import re

# KC: "DP442 name is 'HILLSIDE WEST PHASE 3' (applies throughout)".
# Add a row whenever a development package's proper name is confirmed.
DP_NAMES = {
    "DP442": "HILLSIDE WEST PHASE 3",
}

# RAT: "Reducer label is always 'larger X smaller' to match what would be on a
# BOM for the item (e.g. 250x125)."
REDUCER = re.compile(r"\b(?P<a>\d{2,4})\s*[xX]\s*(?P<b>\d{2,4})\b")

# A valve tag that nearly parses: the client found a "VB" typo on DP442.
VALVE_TAG_OK = re.compile(
    r"^(?:RG|RW|PG|PW)?\d{2,4}(?:VB|VF|VG)\d{1,3}[A-Z]?$"
)
VALVE_TAG_NEARLY = re.compile(
    r"^(?:RG|RW|PG|PW)?\d{2,4}(?P<kind>[A-Z]{1,3})\d{1,3}[A-Z]?$"
)
# Things that match "nearly" but are legitimately something else.
NOT_A_VALVE = re.compile(r"^\d{2,4}(?:MM|M|OD|DN|SDR|PN)\d*$", re.IGNORECASE)


def _valve_like(kind: str) -> bool:
    """Is this letter group a plausible mistyping of VB / VF / VG?

    The catch this check exists for is RG250B16 -- the V dropped. Queensland
    lot-on-plan property references share the whole tag shape (95DY462 is lot
    95 on survey plan DY462, drawn beside the property boundary), so a letter
    group with no connection to a valve kind must not fire. Valve-like means
    it still starts with a V, or is a bare B/F/G with the V dropped.
    """
    kind = kind.upper()
    return kind.startswith("V") or kind in {"B", "F", "G"}

LINE_NUMBER = re.compile(
    r"\b(?P<size>\d{2,4})-(?P<service>[A-Z]{2})-(?P<seq>[A-Z0-9]+)"
    r"-(?P<spec>[A-Z]\d{2,3})-(?P<project>\d{4})\b"
)
PLACEHOLDER_SEQ = re.compile(r"^[X?]+$", re.IGNORECASE)


def run_client_checks(sheet, tb, add) -> None:
    from .checks import FAIL, NA, PASS, REVIEW
    from .content_checks import body_items

    items = body_items(sheet)
    blob = " ".join(i.text for i in items)

    _check_dp_name(sheet, tb, add)
    _check_reducers(items, add)
    _check_valve_tag_spelling(items, add)
    _check_duplicate_sequences(blob, add)


def _check_dp_name(sheet, tb, add) -> None:
    """KC: the development package's name must be its full official name."""
    from .checks import FAIL, NA, PASS

    ref = "Rows 15, 39"
    title = "Development package name matches its official name"
    title_text = tb.title.upper()
    found = [dp for dp in DP_NAMES if dp in title_text]
    if not found:
        add("CMT-01", "Title Block", title, NA,
            "No development package named in the title.", ref)
        return

    problems = []
    for dp in found:
        expected = DP_NAMES[dp]
        if expected not in title_text:
            # What does the title actually call it?
            after = title_text.split(dp, 1)[1].strip(" -")
            problems.append(
                f"the title calls {dp} \"{after[:60]}\" but its name is "
                f"\"{expected}\""
            )
    # Anchor on the title line naming the DP. The markup pass's own text
    # search only looks in the drawing body, and the title sits within a few
    # points of the reference-drawing table, where a callout would mislead.
    lines = {l.strip().upper() for l in tb.title_lines}
    anchors = [
        (i.x0, i.y0, i.x1, i.y1) for i in sheet.items
        if i.text.strip().upper() in lines
        and any(dp in i.text.upper() for dp in found)
    ]
    add("CMT-01", "Title Block", title,
        FAIL if problems else PASS,
        "; ".join(problems) or
        f"{', '.join(f'{dp} = {DP_NAMES[dp]}' for dp in found)}.",
        ref, anchors=anchors if problems else None)


def _check_reducers(items, add) -> None:
    """RAT: reducer labels read larger x smaller, e.g. 250x125."""
    from .checks import NA, PASS, REVIEW

    ref = "Row 63"
    title = "Reducer labels read larger x smaller"
    wrong, seen = [], 0
    for item in items:
        text = item.text.strip()
        for match in REDUCER.finditer(text):
            a, b = int(match["a"]), int(match["b"])
            if a == b:
                continue
            seen += 1
            if a < b:
                wrong.append(f"{match.group(0)} (should read {b}x{a})")
    if not seen:
        add("CMT-02", "Drawing Body", title, NA,
            "No reducer labels on this sheet.", ref)
    elif wrong:
        add("CMT-02", "Drawing Body", title, REVIEW,
            "Reducer label(s) read smaller x larger: " + "; ".join(sorted(set(wrong)))
            + ". The client asks for larger x smaller, to match the BOM.", ref)
    else:
        add("CMT-02", "Drawing Body", title, PASS,
            f"{seen} reducer label(s), all larger x smaller.", ref)


def _check_valve_tag_spelling(items, add) -> None:
    """RAT: '"VB" Typo.' -- a valve tag that nearly parses is mistyped."""
    from .checks import FAIL, NA, PASS

    ref = "Rows 62, 63"
    title = "Valve tags spelled correctly"
    good, suspect, anchors = 0, [], []
    for item in items:
        text = item.text.strip()
        if VALVE_TAG_OK.match(text):
            good += 1
        elif ((m := VALVE_TAG_NEARLY.match(text)) and _valve_like(m["kind"])
              and not NOT_A_VALVE.match(text)):
            suspect.append(text)
            # A mistyped tag cannot be found by the tag pattern later, so
            # carry its position with the finding.
            anchors.append((item.x0, item.y0, item.x1, item.y1))
    if not good and not suspect:
        add("CMT-03", "Drawing Body", title, NA, "No valve tags on this sheet.", ref)
    elif suspect:
        add("CMT-03", "Drawing Body", title, FAIL,
            "Tag(s) that look like a mistyped valve tag - the valve type must "
            "be VB (ball), VF (butterfly) or VG (gate): "
            + ", ".join(sorted(set(suspect))), ref, anchors)
    else:
        add("CMT-03", "Drawing Body", title, PASS,
            f"{good} valve tag(s), all well formed.", ref)


def _check_duplicate_sequences(blob: str, add) -> None:
    """KC: 'is this number 2374 accidentally duplicated?'

    A sequence number identifies a line, so the same sequence appearing on two
    lines of different size, service or spec means one of them is wrong.
    """
    from .checks import NA, PASS, REVIEW

    ref = "Rows 24, 54"
    title = "Line sequence numbers not duplicated"
    # Keep the whole line number, so the markup pass can find it on the sheet.
    by_sequence: dict[str, set[str]] = {}
    for match in LINE_NUMBER.finditer(blob):
        if PLACEHOLDER_SEQ.match(match["seq"]):
            continue
        by_sequence.setdefault(match["seq"], set()).add(match.group(0))

    clashes = {
        seq: lines for seq, lines in by_sequence.items()
        if len({tuple(l.split("-")[:2] + l.split("-")[3:4]) for l in lines}) > 1
    }
    if not by_sequence:
        add("LN-03", "Drawing Body", title, NA,
            "No line numbers with an assigned sequence on this sheet.", ref)
    elif clashes:
        add("LN-03", "Drawing Body", title, REVIEW,
            "; ".join(
                f"sequence {seq} used on " + " and ".join(sorted(lines))
                for seq, lines in sorted(clashes.items())
            )
            + ". Confirm the repeat is deliberate.", ref)
    else:
        add("LN-03", "Drawing Body", title, PASS,
            f"{len(by_sequence)} assigned sequence number(s), all distinct.", ref)


# -- Client comments that still need a human ---------------------------------
#
# These were raised on the DP442 checkprint but depend on topology or intent
# that the text layer does not carry:
#
#   RAT: "Move reducer to upstream side of tee."
#   RAT: "Move these two spec breaks to a single spec break downstream of tee."
#   RAT: "When tie-ing into a VAF, the spec break should always be at the
#         blind, as that is where the pressure test boundary is."
#   RAT: "Remove this valve. There is no vent valve on a hot tap assembly...
#         refer to Q-1000-50-DH-688."
#   RAT: "Spec break is the wrong way around."
#   KC:  "reverse the off page connectors"
#   Ewan: "remove clash of cloud and tag", "check against base dwg if cloud
#         should be removed", "do we need a new line number here?"
#
# Implementing these would need the pipe network reconstructed from the vector
# geometry, which was measured and rejected -- see geometry_checks.py.
