"""Read the Origin A1 drawing-frame fields out of an extracted sheet.

Everything here is anchored on the template's own printed labels ("DRAWING
NO.", "REVISION", "DRAWN", ...) and searched in a window expressed relative to
that label, rather than at absolute coordinates.  Drawings coming out of
different DP packages sit 1-3pt apart on the sheet, which absolute windows
would not survive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .extract import Sheet, TextItem, _norm

# The frame region occupied by the title block, as fractions of the page.
TITLE_BLOCK = (0.66, 0.85, 1.0, 1.0)
# Revision history + reference drawing tables run along the bottom left.
BOTTOM_TABLES = (0.0, 0.88, 0.66, 1.0)

SIGNOFF_ROLES = [
    "DRAWN",
    "DWG CHECK",
    "DESIGN",
    "ENG DES CHECK",
    "DRAFT. SUPER.",
    "PROJECT APPROVAL",
]

REV_TABLE_ROLES = [
    "DRAWN",
    "DWG CHECK",
    "DESIGN",
    "ENG DES CHECK",
    "OE DRAFT SUPER",
    "OE PROJECT APPROVAL",
]

# Printed template labels, excluded when hunting for a field's *value* so that
# a neighbouring caption is never mistaken for data.
TEMPLATE_LABELS = {
    _norm(t)
    for t in [
        "TITLE", "BY", "DATE", "DRAWING NO.", "REVISION", "PROJECT NO.",
        "MOD NO.", "SCALE", "CADFILE", "REV", "REVISION DESCRIPTION",
        "REFERENCE DRAWING TITLE", "APPROVALS", "PROJECT APPROVALS - OTHERS",
        "PREPARED FOR ORIGIN BY:", "PROJECT", "DRAWING OFFICE", "O R I G I N",
        *SIGNOFF_ROLES,
        *REV_TABLE_ROLES,
    ]
}

# The sheet separator is "/" on the drawing but "_" in filenames and in some
# reference tables, because "/" is illegal in a Windows filename. Both forms
# name the same drawing -- compare with normalise_drawing_number().
ORIGIN_DWG_NO = re.compile(
    r"\b[A-Z]-\d{4}-\d{2}-[A-Z]{2}-\d{2,6}(?:[/_-]\d{1,2})?(?:P\d{2})?\b"
)
# Same pattern without the closing word boundary, for reading a cell where the
# revision is butted straight onto the number ("Q-4255-10-DF-030/13P03A").
# With the \b the match would backtrack to the shortest form that ends on a
# boundary -- "Q-4255-10-DF-030" -- silently losing the sheet and P suffix.
ORIGIN_DWG_NO_LEAD = re.compile(
    r"[A-Z]-\d{4}-\d{2}-[A-Z]{2}-\d{2,6}(?:[/_-]\d{1,2})?(?:P\d{2})?"
)


def normalise_drawing_number(number: str) -> str:
    """Compare drawing numbers regardless of the sheet separator used.

    The sheet separator is ``/`` on the drawing and ``_`` in filenames
    (Windows forbids ``/``), and it may be followed by a project modifier:
    ``Q-4255-10-DF-030_13P03`` names the same sheet as
    ``Q-4255-10-DF-030/13P03``.
    """
    return re.sub(r"[_-](\d{1,2})(P\d{2})?$", r"/\1\2",
                  number.strip().upper())
# Origin revisions seen in practice: A, B (review), P01 (preliminary),
# 0, 1, 2 (issued) and 0A, 1A (an issued revision re-issued for review).
REVISION_VALUE = re.compile(r"^(?:P\d{1,2}|\d{1,2}[A-Z]?|[A-Z])$")
SHEET_SIZE = re.compile(r"^A[0-5]$")
DATE_VALUE = re.compile(r"^\d{2}[./]\d{2}[./]\d{4}$")


@dataclass
class SignOff:
    role: str
    initials: str = ""
    date: str = ""

    @property
    def populated(self) -> bool:
        return bool(self.initials and self.date)


@dataclass
class RevisionRow:
    revision: str = ""
    date: str = ""
    description: str = ""
    approvals: dict[str, str] = field(default_factory=dict)


@dataclass
class TitleBlock:
    found: bool = False
    drawing_number: str = ""
    revision: str = ""
    project_no: str = ""
    scale: str = ""
    sheet_size: str = ""
    cadfile_cell: str = ""
    cadfile_border: str = ""
    title_lines: list[str] = field(default_factory=list)
    signoffs: list[SignOff] = field(default_factory=list)
    revision_history: list[RevisionRow] = field(default_factory=list)
    reference_drawings: list[tuple[str, str]] = field(default_factory=list)
    derived_from: str = ""

    @property
    def title(self) -> str:
        return " / ".join(self.title_lines)

    def signoff(self, role: str) -> SignOff | None:
        wanted = _norm(role)
        for s in self.signoffs:
            if _norm(s.role) == wanted:
                return s
        return None


def _is_label(item: TextItem) -> bool:
    return _norm(item.text) in TEMPLATE_LABELS


def _value_near(
    sheet: Sheet,
    anchor: TextItem,
    dx: tuple[float, float],
    dy: tuple[float, float],
    *,
    min_height: float = 0.0,
    max_height: float = 1e9,
    pick: str = "first",
) -> TextItem | None:
    """Single value item in an anchor-relative window, labels excluded."""
    cands = [
        i
        for i in sheet.near(
            anchor, dx=dx, dy=dy, min_height=min_height, max_height=max_height
        )
        if not _is_label(i)
    ]
    if not cands:
        return None
    if pick == "tallest":
        return max(cands, key=lambda i: i.height)
    return cands[0]


def _text(item: TextItem | None) -> str:
    return item.text.strip() if item else ""


def _rows_by_y(items: list[TextItem], tol: float = 4.0) -> list[list[TextItem]]:
    """Group items into visual rows on their top edge."""
    rows: list[list[TextItem]] = []
    for item in sorted(items, key=lambda i: (i.y0, i.x0)):
        for row in rows:
            if abs(row[0].y0 - item.y0) <= tol:
                row.append(item)
                break
        else:
            rows.append([item])
    for row in rows:
        row.sort(key=lambda i: i.x0)
    return rows


def parse(sheet: Sheet) -> TitleBlock:
    """Extract every frame field this tool knows how to read."""
    tb = TitleBlock()
    if not sheet.has_text_layer:
        return tb

    block = sheet.region(*TITLE_BLOCK, frac=True)
    bottom = sheet.region(*BOTTOM_TABLES, frac=True)

    # -- drawing number / revision / project / scale --------------------
    if a := sheet.find_label("DRAWING NO.", within=block):
        value = _text(
            _value_near(sheet, a, (-6, 22), (-3, 35), min_height=14, pick="tallest")
        )
        # An OCR text layer reports inflated word boxes, so the drawing number
        # can arrive glued to the revision ("Q-4255-10-DF-572P01 A"). Take the
        # drawing number by pattern rather than assuming the cell is clean.
        if match := ORIGIN_DWG_NO_LEAD.search(value.upper()):
            tb.drawing_number = match.group(0)
            trailing = value.upper()[match.end():].strip()
            if REVISION_VALUE.match(trailing):
                tb.revision = trailing
        else:
            tb.drawing_number = value
    if a := sheet.find_label("REVISION", within=block):
        tb.revision = _text(
            _value_near(sheet, a, (-10, 22), (2, 24), min_height=11, pick="tallest")
        )
    if a := sheet.find_label("PROJECT NO.", within=block):
        tb.project_no = _text(_value_near(sheet, a, (-6, 40), (2, 18)))
    if a := sheet.find_label("SCALE", within=block):
        tb.scale = _text(_value_near(sheet, a, (14, 60), (-5, 12)))
    if a := sheet.find_label("CADFILE", within=block):
        tb.cadfile_cell = _text(_value_near(sheet, a, (-6, 120), (-5, 12)))

    for item in block:
        if SHEET_SIZE.match(item.text.strip()):
            tb.sheet_size = item.text.strip()
            break

    # -- title lines ----------------------------------------------------
    if a := sheet.find_label("TITLE", within=block):
        lines = sheet.near(a, dx=(-4, 22), dy=(2, 50), min_height=11)
        tb.title_lines = [i.text.strip() for i in lines if not _is_label(i)]

    # -- title block sign-off boxes -------------------------------------
    # The date column sits immediately right of the initials column and the
    # title lines start just beyond it, so the date window has to stay tight.
    for role in SIGNOFF_ROLES:
        a = sheet.find_label(role, within=block)
        if not a:
            continue
        dates = [
            i
            for i in sheet.near(a, dx=(44, 64), dy=(-7, 8))
            if not _is_label(i)
        ]
        dated = [i for i in dates if DATE_VALUE.match(i.text.strip())]
        tb.signoffs.append(
            SignOff(
                role=role,
                initials=_text(_value_near(sheet, a, (25, 42), (-6, 8))),
                date=_text((dated or dates or [None])[0]),
            )
        )

    # -- CADFILE printed under the drawing border -----------------------
    for item in sheet.items:
        if ".DWG" in item.text.upper():
            tb.cadfile_border = item.text.strip()
            break

    # -- "drawing derived from" statement -------------------------------
    for item in sheet.items:
        if "DERIVED FROM" in item.text.upper():
            tb.derived_from = item.text.strip()
            break

    tb.revision_history = _parse_revision_history(sheet, bottom)
    tb.reference_drawings = _parse_reference_drawings(sheet, bottom)

    if not tb.drawing_number or not tb.title_lines:
        _positional_fallback(sheet, tb)
    if not tb.revision_history:
        tb.revision_history = _positional_revision_history(sheet)
    if not tb.reference_drawings:
        tb.reference_drawings = _positional_reference_drawings(sheet)

    tb.found = bool(tb.drawing_number or tb.title_lines)
    return tb


# Where the frame's fields sit, as fractions of the sheet. Measured on both the
# SHX-captured DP442 drawings and the TrueType-plotted IF439 ones, which agree
# to within a few tenths of a percent.
TITLE_LINES_BAND = (0.74, 0.855, 0.79, 0.915)     # x0 range, y0 range
DRAWING_NO_BAND = (0.78, 0.905, 0.84, 0.950)
REVISION_BAND = (0.925, 0.905, 0.985, 0.950)
# Title text plots at about 5pt; DXF-derived heights land just under, so the
# floor has to sit below that rather than on it.
MIN_TITLE_HEIGHT = 4.0
# Bottom tables: rows sit above their headers, between these y fractions.
BOTTOM_ROWS_Y = (0.900, 0.945)
REF_NUMBER_X = (0.025, 0.080)
REF_TITLE_X = (0.075, 0.210)
REV_LETTER_X = (0.218, 0.238)
REV_APPROVALS_X = {           # column start fractions, in table order
    "DRAWN": 0.399, "DWG CHECK": 0.413, "DESIGN": 0.428,
    "ENG DES CHECK": 0.442, "OE DRAFT SUPER": 0.456,
    "OE PROJECT APPROVAL": 0.470,
}
APPROVAL_COLUMN_WIDTH = 0.014


def _positional_fallback(sheet: Sheet, tb: TitleBlock) -> None:
    """Read the frame by position when its printed labels are not in the text.

    A drawing plotted with TrueType annotation text still has its title block
    *template* drawn in SHX, so "DRAWING NO." and "TITLE" are stroked geometry
    and there is nothing to anchor on -- even though the values themselves are
    perfectly readable. The Origin frame layout is fixed, so fall back to
    where the fields are rather than what they sit next to.
    """
    W, H = sheet.width, sheet.height

    if not tb.title_lines:
        x0, y0, x1, y1 = TITLE_LINES_BAND
        candidates = [
            i for i in sheet.items
            if x0 <= i.x0 / W <= x1 and y0 <= i.y0 / H <= y1
            and i.height >= MIN_TITLE_HEIGHT and not _is_label(i)
        ]
        if candidates:
            # The title lines are left-aligned in their cell; anything starting
            # further right belongs to another column.
            left = min(i.x0 for i in candidates)
            tb.title_lines = [
                i.text.strip()
                for i in sorted(candidates, key=lambda i: i.y0)
                if abs(i.x0 - left) <= 6
            ]

    if not tb.drawing_number:
        x0, y0, x1, y1 = DRAWING_NO_BAND
        candidates = [
            i for i in sheet.items
            if x0 <= i.x0 / W <= x1 and y0 <= i.y0 / H <= y1 and i.height >= 12
        ]
        if candidates:
            cell = max(candidates, key=lambda i: i.height).text.strip()
            if match := ORIGIN_DWG_NO_LEAD.search(cell.upper()):
                tb.drawing_number = match.group(0)
                # The revision cell abuts the number, and with no space between
                # them the two arrive as one string ("...P03A").
                trailing = cell.upper()[match.end():].strip()
                if not tb.revision and REVISION_VALUE.match(trailing):
                    tb.revision = trailing
            else:
                tb.drawing_number = cell

    if not tb.revision:
        x0, y0, x1, y1 = REVISION_BAND
        cells = [
            i.text.strip() for i in sheet.items
            if x0 <= i.x0 / W <= x1 and y0 <= i.y0 / H <= y1
            and REVISION_VALUE.match(i.text.strip())
        ]
        if cells:
            tb.revision = cells[0]


def _parse_revision_history(sheet: Sheet, bottom: list[TextItem]) -> list[RevisionRow]:
    rev_hdr = sheet.find_label("REV", within=bottom)
    date_hdr = sheet.find_label("DATE", within=bottom)
    drawn_hdr = sheet.find_label("DRAWN", within=bottom)
    if not (rev_hdr and date_hdr and drawn_hdr):
        return []

    # Approval initials sit within a few points of their (narrow) header; the
    # wide description column is bounded by the two headers either side of it.
    columns: dict[str, tuple[float, float]] = {
        "date": (date_hdr.x0 - 12, date_hdr.x0 + 12),
        "description": (date_hdr.x1 + 1, drawn_hdr.x0 - 6),
    }
    for role in REV_TABLE_ROLES:
        hdr = sheet.find_label(role, within=bottom)
        if hdr:
            columns[role] = (hdr.x0 - 6, hdr.x0 + 11)

    rev_cells = sorted(
        (
            i
            for i in bottom
            if rev_hdr.x0 - 8 <= i.x0 <= rev_hdr.x0 + 11
            and rev_hdr.y0 - 70 < i.y0 < rev_hdr.y0 - 2
            and REVISION_VALUE.match(i.text.strip())
        ),
        key=lambda i: i.y0,  # newest revision at the top of the table
    )
    if not rev_cells:
        return []

    # Assign each cell to its nearest revision row rather than to any row
    # within a tolerance -- the rows are only 5-8pt apart.
    bands: dict[int, list[TextItem]] = {n: [] for n in range(len(rev_cells))}
    for item in bottom:
        if item in rev_cells or item.y0 >= rev_hdr.y0 - 2 or _is_label(item):
            continue
        n = min(range(len(rev_cells)), key=lambda k: abs(rev_cells[k].y0 - item.y0))
        if abs(rev_cells[n].y0 - item.y0) <= 6:
            bands[n].append(item)

    rows: list[RevisionRow] = []
    for n, cell in enumerate(rev_cells):
        row = RevisionRow(revision=cell.text.strip())
        for name, (x0, x1) in columns.items():
            value = " ".join(
                i.text.strip() for i in sorted(bands[n], key=lambda i: i.x0)
                if x0 <= i.x0 <= x1
            )
            if name == "date":
                row.date = value
            elif name == "description":
                row.description = value
            else:
                row.approvals[name] = value
        rows.append(row)
    return rows


def _parse_reference_drawings(
    sheet: Sheet, bottom: list[TextItem]
) -> list[tuple[str, str]]:
    hdr = sheet.find_label("REFERENCE DRAWING TITLE", within=bottom)
    no_hdr = sheet.find_label("DRAWING NO.", within=bottom)
    rev_hdr = sheet.find_label("REV", within=bottom)
    if not (hdr and no_hdr):
        return []
    right = rev_hdr.x0 - 15 if rev_hdr else hdr.x1 + 100
    top = hdr.y0 - 70

    # Rows are anchored on a well-formed drawing number in the left column --
    # the Origin address logo block overlaps this band and would otherwise be
    # read as table content.
    numbers = [
        i
        for i in bottom
        if no_hdr.x0 - 20 <= i.x0 <= no_hdr.x0 + 20
        and top < i.y0 < hdr.y0 - 2
        and ORIGIN_DWG_NO.match(i.text.strip())
    ]

    refs: list[tuple[str, str]] = []
    for cell in sorted(numbers, key=lambda i: i.y0):
        band = [
            i
            for i in bottom
            if abs(i.y0 - cell.y0) <= 4
            and hdr.x0 - 60 <= i.x0 <= right
            and i.source == cell.source  # keeps the TrueType logo text out
            and not _is_label(i)
        ]
        title = " ".join(i.text.strip() for i in sorted(band, key=lambda i: i.x0))
        refs.append((cell.text.strip(), title))
    return refs


def _positional_revision_history(sheet: Sheet) -> list[RevisionRow]:
    """Revision history read by column position, for frames with SHX headers."""
    W, H = sheet.width, sheet.height
    y0, y1 = BOTTOM_ROWS_Y
    band = [i for i in sheet.items if y0 <= i.y0 / H <= y1]
    if not band:
        return []

    rows: list[RevisionRow] = []
    for cell in sorted(band, key=lambda i: i.y0):
        if not (REV_LETTER_X[0] <= cell.x0 / W <= REV_LETTER_X[1]):
            continue
        if not REVISION_VALUE.match(cell.text.strip()):
            continue
        row = RevisionRow(revision=cell.text.strip())
        line = [i for i in band if abs(i.y0 - cell.y0) <= 5 and i is not cell]

        # Date and description share a cell when the words merge, so read the
        # date out of the text rather than relying on a column boundary.
        left = " ".join(
            i.text.strip() for i in sorted(line, key=lambda i: i.x0)
            if i.x0 / W < min(REV_APPROVALS_X.values()) - 0.005
        )
        if match := re.search(r"\d{1,2}[./]\d{1,2}[./]\d{4}", left):
            row.date = match.group(0)
            row.description = left[match.end():].strip()
        else:
            row.description = left.strip()

        for role, start in REV_APPROVALS_X.items():
            row.approvals[role] = " ".join(
                i.text.strip() for i in sorted(line, key=lambda i: i.x0)
                if start - 0.004 <= i.x0 / W <= start + APPROVAL_COLUMN_WIDTH
            )
        rows.append(row)
    return rows


def _positional_reference_drawings(sheet: Sheet) -> list[tuple[str, str]]:
    """Reference drawings read by column position."""
    W, H = sheet.width, sheet.height
    y0, y1 = BOTTOM_ROWS_Y
    band = [i for i in sheet.items if y0 <= i.y0 / H <= y1]

    refs: list[tuple[str, str]] = []
    for cell in sorted(band, key=lambda i: i.y0):
        if not (REF_NUMBER_X[0] <= cell.x0 / W <= REF_NUMBER_X[1]):
            continue
        if not ORIGIN_DWG_NO_LEAD.match(cell.text.strip().upper()):
            continue
        title = " ".join(
            i.text.strip() for i in sorted(band, key=lambda i: i.x0)
            if abs(i.y0 - cell.y0) <= 5
            and REF_TITLE_X[0] <= i.x0 / W <= REF_TITLE_X[1]
        )
        refs.append((cell.text.strip(), title))
    return refs
