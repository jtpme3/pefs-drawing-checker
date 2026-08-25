"""Checks on the numbered notes: references and cross-set consistency.

Driven by client comments on the DP442 IFR checkprint ("is note 5 still
relevant?") and by the requirement to keep note wording identical across a
drawing set -- the same note appearing as "HPV LOCATED..." on one sheet and
"HPV TO BE LOCATED..." on the next is the kind of drift a reader notices and a
checker misses.

Notes are deliberately NOT compared against a standard notes list. That was
tried (NOTE-02) and flagged 11 of 14 real sheets for legitimate wording, which
Jordan called noise (2026-08-11). Internal consistency is the check: if one
drawing words a note differently from the rest of the set, raise that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .extract import dedupe_shx_repeat

# "NOTES: 1. FOO 2. BAR" -> the numbers and their text.
NOTE_SPLIT = re.compile(r"(?:^|\s)(\d{1,2})\.\s+")
NOTE_BLOCK = re.compile(r"^\s*(?:GENERAL\s+)?NOTES?\s*:", re.IGNORECASE)
# "NOTE 6" / "NOTES 6 & 7" callouts in the drawing body.
NOTE_REFERENCE = re.compile(r"\bNOTES?\s+(\d{1,2})(?:\s*[&,]\s*(\d{1,2}))*\b", re.I)
DELETED_NOTE = re.compile(r"^\s*DELETED[\s.]*$", re.IGNORECASE)
# Notes that state a blanket rule are not expected to be pointed at.
GENERAL_NOTE = re.compile(
    r"^\s*(?:ALL\b|UNLESS\b|REFER\b|STANDARD\b|FOR\b|WHERE\b)", re.IGNORECASE
)

STOPWORDS = {
    "TO", "BE", "THE", "A", "AN", "OF", "IN", "ON", "AT", "IS", "ARE", "AND",
    "OR", "SHALL", "WILL", "MAY", "THIS", "THAT", "IT", "AS", "BY", "FROM",
    "WITH", "FOR", "HAS", "HAVE", "BEEN", "WAS", "WERE",
}
# Two notes counted as "the same note, worded differently" above this overlap.
SAME_NOTE_SIMILARITY = 0.72
# ...or when they open on the same subject and still overlap substantially.
# The LPD collection note is the case that needs this: the standard wording and
# the older one share only 54% of their words but both open "LPD COLLECTION",
# and the client asks for the standard one to be used.
LEAD_IN_WORDS = 2
LEAD_IN_SIMILARITY = 0.35


@dataclass
class Note:
    number: int
    text: str

    @property
    def is_deleted(self) -> bool:
        return bool(DELETED_NOTE.match(self.text))

    @property
    def is_general(self) -> bool:
        return bool(GENERAL_NOTE.match(self.text))


def signature(text: str) -> frozenset[str]:
    """Significant words, so wording drift does not hide a shared note."""
    words = re.findall(r"[A-Z0-9]{2,}", text.upper())
    return frozenset(w for w in words if w not in STOPWORDS)


def similarity(a: str, b: str) -> float:
    sa, sb = signature(a), signature(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def lead_in(text: str) -> tuple[str, ...]:
    """The first few significant words -- what the note is *about*."""
    words = [
        w for w in re.findall(r"[A-Z0-9]{2,}", text.upper()) if w not in STOPWORDS
    ]
    return tuple(words[:LEAD_IN_WORDS])


def same_note(a: str, b: str) -> bool:
    """Whether two notes are the same note, however differently worded."""
    score = similarity(a, b)
    if score >= SAME_NOTE_SIMILARITY:
        return True
    return (
        score >= LEAD_IN_SIMILARITY
        and lead_in(a) == lead_in(b)
        and len(lead_in(a)) == LEAD_IN_WORDS
    )


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().rstrip(".")).upper()


def extract_notes(sheet) -> list[Note]:
    """Numbered notes from every NOTES: block on the sheet."""
    notes: dict[int, str] = {}
    for item in sheet.items:
        text = item.text.strip()
        if not NOTE_BLOCK.match(text):
            continue
        parts = NOTE_SPLIT.split(dedupe_shx_repeat(text))
        for i in range(1, len(parts) - 1, 2):
            number = int(parts[i])
            body = parts[i + 1].strip()
            # A later block wins only if it carries more text.
            if len(body) > len(notes.get(number, "")):
                notes[number] = body
    return [Note(n, t) for n, t in sorted(notes.items())]


def referenced_numbers(sheet) -> set[int]:
    """Note numbers called out in the drawing body."""
    found: set[int] = set()
    for item in sheet.items:
        text = item.text.strip()
        if NOTE_BLOCK.match(text):
            continue  # the notes block itself is not a reference
        for match in NOTE_REFERENCE.finditer(text.upper()):
            found.update(int(g) for g in match.groups() if g)
    return found


def run_note_checks(sheet, tb, add) -> None:
    from .checks import FAIL, NA, PASS, REVIEW

    notes = extract_notes(sheet)
    refs = referenced_numbers(sheet)
    # Note findings have no tag to point at, so pin them to the notes block.
    blocks = [
        (i.x0, i.y0, i.x1, i.y1) for i in sheet.items
        if NOTE_BLOCK.match(i.text.strip())
    ]

    if not notes:
        add("NOTE-01", "Notes & Holds", "Note references resolve", NA,
            "No numbered notes on this sheet.", "Rows 76, 78")
        return

    numbers = {n.number for n in notes}

    # -- references resolve ------------------------------------------------
    missing = sorted(refs - numbers)
    gaps = sorted(n for n in range(1, max(numbers) + 1) if n not in numbers)
    problems = []
    if missing:
        problems.append(
            f"the drawing calls up NOTE {', '.join(map(str, missing))} but the "
            f"notes block only goes to {max(numbers)}"
        )
    if gaps:
        problems.append(f"note number(s) {', '.join(map(str, gaps))} missing from the block")

    # An unreferenced note that is neither a DELETED placeholder nor a blanket
    # statement is one the client has asked about before ("is note 5 still
    # relevant?").
    orphans = [
        n for n in notes
        if n.number not in refs and not n.is_deleted and not n.is_general
    ]

    if problems:
        add("NOTE-01", "Notes & Holds", "Note references resolve", FAIL,
            "; ".join(problems), "Rows 76, 78", blocks)
    elif orphans:
        add("NOTE-01", "Notes & Holds", "Note references resolve", REVIEW,
            "Note(s) not called up anywhere on the drawing - confirm they are "
            "still relevant: "
            + "; ".join(f"{n.number}. {n.text[:90]}" for n in orphans),
            "Rows 76, 78", blocks)
    else:
        add("NOTE-01", "Notes & Holds", "Note references resolve", PASS,
            f"{len(notes)} note(s), {len(refs)} reference(s), all resolve.",
            "Rows 76, 78")


def _first_difference(a: str, b: str) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def check_note_consistency(entries) -> None:
    """Cross-drawing note wording. Appends NOTE-03 to each report in place."""
    from .checks import NA, PASS, Result, REVIEW

    checked = [(r, s, t) for r, s, t in entries if not r.skipped]
    if len(checked) < 2:
        for report, _, _ in checked:
            report.results.append(Result(
                "NOTE-03", "Notes & Holds", "Note wording consistent across the set",
                NA, "Only one drawing in this run - nothing to compare against.",
                "Rows 76, 78",
            ))
        return

    # Collect every note, then cluster them by similarity so the same note
    # worded two ways lands in one group.
    collected: list[tuple[str, Note]] = []
    for report, sheet, _ in checked:
        for note in extract_notes(sheet):
            if not note.is_deleted:
                collected.append((report.label, note))

    groups: list[list[tuple[str, Note]]] = []
    for label, note in collected:
        for group in groups:
            if same_note(note.text, group[0][1].text):
                group.append((label, note))
                break
        else:
            groups.append([(label, note)])

    # A group with more than one distinct wording is drift. The majority
    # wording is taken as correct and the odd ones out are reported.
    drift: dict[str, list[str]] = {}
    for group in groups:
        wordings: dict[str, list[str]] = {}
        for label, note in group:
            wordings.setdefault(normalise(note.text), []).append(label)
        if len(wordings) < 2:
            continue
        # Consistency only -- no external "standard" wording is consulted.
        # The majority wording is taken as the set's intent and the odd ones
        # out are flagged; which wording is actually right is the engineer's
        # call, and the finding quotes both so they can make it.
        majority = max(wordings, key=lambda w: len(wordings[w]))
        for wording, labels in wordings.items():
            if wording == majority:
                continue
            # Quote from where the two wordings diverge, so the difference is
            # visible rather than buried past the end of a truncated quote.
            at = _first_difference(wording, majority)
            start = max(0, at - 30)
            for label in labels:
                drift.setdefault(label, []).append(
                    f'"...{wording[start:start + 130]}" - '
                    f'{len(wordings[majority])} other drawing(s) word this note '
                    f'"...{majority[start:start + 130]}"'
                )

    for report, _, _ in checked:
        issues = drift.get(report.label)
        report.results.append(Result(
            "NOTE-03", "Notes & Holds", "Note wording consistent across the set",
            REVIEW if issues else PASS,
            "; ".join(issues) if issues
            else f"note wording matches the rest of the {len(checked)}-drawing set.",
            "Rows 76, 78",
        ))
