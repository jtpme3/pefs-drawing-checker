"""Spell checking for drawing prose.

A plain dictionary check over a PEFS is useless -- roughly a quarter of the
words on the sheet are tags, initials, service codes and Origin vocabulary, so
it flags ~77 "errors" of which none are real.  Three things make it usable:

1. Only prose fields are checked: title lines, notes, revision descriptions and
   reference drawing titles.  Tags, line numbers and file paths are never
   treated as words.
2. A curated domain dictionary covers the vocabulary these drawings actually
   use.
3. Batch consensus.  A word used across several drawings is house vocabulary,
   however odd it looks.  A word appearing on one drawing only, which is a
   near-miss of a word that is common in the batch or in the dictionary, is a
   typo -- that is exactly the shape of "SYMOLOGY" against "SYMBOLOGY".

The result is a handful of high-confidence flags rather than a wall of noise.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from spellchecker import SpellChecker

# Vocabulary that is correct on an Origin gathering drawing but absent from a
# general English dictionary.  Keep additions here rather than loosening the
# rules below.
DOMAIN_WORDS = {
    # document / drawing vocabulary
    "PEFS", "PEF", "CADFILE", "DWG", "DWGS", "NTS", "STD", "REV", "REVS",
    "HAZOP", "RPEQ", "ABN", "GPO", "QLD", "LNG", "APLNG", "ARN", "DP",
    "PCN", "ESS", "CTR", "IFC", "IFD", "IFU", "IFR", "IFI", "ASB", "MOD",
    # gathering / process vocabulary
    "WELLSITE", "WELLSITES", "WELLPAD", "WELLPADS", "POWERLINE", "POWERLINES",
    "PIPEWORK", "SPOOL", "SPOOLS", "SDR", "OD", "ROW", "LPD", "LPDS", "HPV",
    "HPVS", "DIP", "DIPS", "TIP", "TIPS", "VAF", "PSV", "PSE", "PSH", "PSL",
    "TIE", "TIEIN", "HPC", "PLC", "HVC", "RC", "WC", "RG", "RW", "PG", "PW",
    "KPAG", "KPA", "MPA", "BARG", "DN", "NB", "SDRS",
    "OFFTAKE", "OFFTAKES", "RISER", "RISERS", "TRENCH", "TRENCHES",
    "CATCHMENT", "GATHERING", "UPSTREAM", "SUSTAIN", "COMMISSIONING",
    "DECOMMISSIONING", "OPERABILITY", "SCHEMATIC", "SCHEMATICS", "KEYPLAN",
    "SYMBOLOGY", "MATCHLINE", "MATCHLINES", "ISOLATABLE", "UNISOLATED",
    "BLIND", "BLINDS", "SPACER", "SPACERS", "SQUEEZE", "STUB", "STUBS",
    "GDA", "MGA", "GIS", "SCADA", "PID", "PIDS",
    "TRUNKLINE", "TRUNKLINES", "FLOWLINE", "FLOWLINES", "WELLHEAD",
    "WELLHEADS", "SEPARABLE", "COMMISSIONING", "DECOMMISSION",
    # Queensland place and asset names seen on these drawings
    "WARREGO", "TOGGS", "WILGAVALE", "HILLSIDE", "CONDABRI", "TALINGA",
    "ORANA", "SPRING", "GULLY", "REEDY", "CREEK", "COMBABULA",
}

# Tokens that are never prose: anything with a digit, an underscore, a slash,
# a dot, or that is a short all-caps code (initials, service codes).
NOT_A_WORD = re.compile(r"[\d_/\\.]")
WORD = re.compile(r"[A-Za-z][A-Za-z']+")

# Words rarer than this in the batch are candidates; anything more common is
# treated as house vocabulary.
MAX_OCCURRENCES_TO_FLAG = 1
MIN_WORD_LENGTH = 5


@dataclass
class Misspelling:
    word: str
    suggestion: str
    context: str

    def __str__(self) -> str:
        return f"'{self.word}' (did you mean '{self.suggestion}'?) in \"{self.context}\""


def prose_fragments(sheet, tb) -> list[str]:
    """Every field on the sheet that could contain words rather than tags.

    Jordan (2026-08-20): spell check ALL text on the drawing, not just the
    title-block prose. Short body labels are included; the token rules below
    (no digits, min length) still keep tags, line numbers and corridor labels
    out. The residual cost, measured on the ARN12 set, is the occasional
    property-owner or locality name raised for review -- accepted.
    """
    fragments: list[str] = []
    fragments.extend(tb.title_lines)
    fragments.extend(r.description for r in tb.revision_history if r.description)
    fragments.extend(t for _, t in tb.reference_drawings if t)

    # Everything drawn on the sheet, excluding the CADFILE path under the
    # border. TrueType 'text' items are the Origin address block, not drawing
    # content, so only SHX/DXF-sourced strings are taken.
    for item in sheet.items:
        text = item.text.strip()
        if text and ".DWG" not in text.upper() and item.source in ("shx", "dxf"):
            fragments.append(text)
    return fragments


def _candidate_words(fragments: list[str]) -> list[tuple[str, str]]:
    """(word, containing fragment) for every token that is genuinely prose."""
    out = []
    for fragment in fragments:
        for token in fragment.split():
            # Strip sentence punctuation first, or the last word of every
            # sentence is discarded as "contains a dot".
            token = token.strip(".,;:!?()[]{}\"'")
            if not token or NOT_A_WORD.search(token):
                continue
            for word in WORD.findall(token):
                if len(word) >= MIN_WORD_LENGTH:
                    out.append((word.upper(), fragment))
    return out


class BatchSpeller:
    """Spell checker calibrated across a whole batch of drawings.

    Build it with every sheet's prose first, then ask it about each sheet --
    the frequency of a word across the batch is what separates house
    vocabulary from a one-off typo.
    """

    def __init__(self) -> None:
        self._spell = SpellChecker()
        self._counts: Counter[str] = Counter()
        self._per_sheet: dict[str, list[tuple[str, str]]] = {}

    def add(self, key: str, fragments: list[str]) -> None:
        words = _candidate_words(fragments)
        self._per_sheet[key] = words
        # Count each word once per sheet, so a word repeated within one
        # drawing does not look like batch-wide vocabulary.
        self._counts.update({w for w, _ in words})

    def _known(self, word: str) -> bool:
        return (
            word in DOMAIN_WORDS
            or not self._spell.unknown([word.lower()])
        )

    def check(self, key: str) -> list[Misspelling]:
        found: list[Misspelling] = []
        seen: set[str] = set()
        for word, fragment in self._per_sheet.get(key, []):
            if word in seen or self._known(word):
                continue
            if self._counts[word] > MAX_OCCURRENCES_TO_FLAG:
                continue  # used on other drawings too -- house vocabulary
            suggestion = self._suggest(word)
            if not suggestion:
                continue  # nothing close: an unusual name, not a typo
            seen.add(word)
            found.append(
                Misspelling(word, suggestion, _snippet(fragment, word))
            )
        return found

    def _suggest(self, word: str) -> str:
        """A near-miss correction, from the dictionary or from the batch."""
        correction = self._spell.correction(word.lower())
        if correction and correction.upper() != word:
            return correction.upper()
        # Fall back to a word that is common in this batch and one edit away.
        for candidate, count in self._counts.most_common():
            if count > MAX_OCCURRENCES_TO_FLAG and _one_edit_apart(word, candidate):
                return candidate
        for candidate in DOMAIN_WORDS:
            if _one_edit_apart(word, candidate):
                return candidate
        return ""


def _one_edit_apart(a: str, b: str) -> bool:
    """True if a and b differ by a single insertion, deletion or substitution."""
    if abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    i = j = 0
    skipped = False
    while i < len(shorter) and j < len(longer):
        if shorter[i] != longer[j]:
            if skipped:
                return False
            skipped = True
            j += 1
            continue
        i += 1
        j += 1
    return True


def _snippet(fragment: str, word: str, width: int = 60) -> str:
    match = re.search(re.escape(word), fragment, re.IGNORECASE)
    if not match:
        return fragment[:width]
    start = max(0, match.start() - width // 2)
    end = min(len(fragment), match.end() + width // 2)
    return ("..." if start else "") + fragment[start:end].strip() + ("..." if end < len(fragment) else "")
