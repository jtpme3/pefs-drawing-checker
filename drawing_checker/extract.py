"""Pull a positioned text layer out of a drawing PDF.

AutoCAD plots Origin drawings with SHX fonts, so the visible text is stroked as
vector geometry and ``page.get_text()`` returns almost nothing.  The saving
grace is that the plot config has "capture SHX fonts as comments" switched on:
every SHX string is also written as a Square annotation carrying the string in
its /Contents and a rect in unrotated page space.  Combining those annotations
with any genuine (TrueType) text gives a complete, positioned text layer with
no OCR involved.

If a PDF ever arrives without that capture option, the text layer comes back
empty and the checks must report NOT CHECKABLE rather than silently passing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz

# A page needs this many strings before it is worth reading at all.
MIN_ITEMS_FOR_TEXT_LAYER = 20
# A plotted drawing sheet is only readable if the SHX capture actually ran.
# The fallback item count covers the (rare) drawing drafted wholly in
# TrueType, where there are no SHX annotations but plenty of real text --
# without it, such a drawing would be wrongly reported as unreadable.
MIN_SHX_FOR_DRAWING = 20
MIN_ITEMS_FOR_DRAWING = 100


@dataclass
class TextItem:
    """One string on the sheet, in displayed (rotation-applied) coordinates."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    source: str  # "shx" (annotation capture) or "text" (real embedded text)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.text[:40]!r} @({self.x0:.0f},{self.y0:.0f}) h={self.height:.0f}>"


@dataclass
class Sheet:
    """A single page of a drawing PDF."""

    pdf_path: Path
    page_index: int
    page_count: int
    width: float
    height: float
    items: list[TextItem] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    vector_count: int = 0
    # True when the text layer was added by an OCR pass rather than by
    # AutoCAD's SHX capture. OCR misreads characters, so findings from such a
    # sheet are capped at REVIEW -- see run_checks.
    is_ocr: bool = False
    raster_coverage: float = 0.0  # largest placed image, as a fraction of the page
    # Set only when the sheet came from a DXF: the blocks, layers and pipe
    # segments a PDF cannot give us, plus the model->page transform.
    dxf: object | None = None
    transform: object | None = None

    @property
    def from_dxf(self) -> bool:
        return self.dxf is not None

    @property
    def filename(self) -> str:
        return self.pdf_path.name

    @property
    def label(self) -> str:
        if self.page_count > 1:
            return f"{self.pdf_path.name} (p{self.page_index + 1})"
        return self.pdf_path.name

    @property
    def shx_count(self) -> int:
        return sum(1 for i in self.items if i.source == "shx")

    @property
    def has_text_layer(self) -> bool:
        """Whether there is enough text here to check the sheet against.

        Drawings are judged on their SHX capture rather than on raw item
        count: the Origin frame carries a TrueType address block that survives
        even when the SHX capture is off, and that handful of words must not
        be mistaken for a readable drawing.
        """
        if self.looks_like_drawing:
            return (
                self.shx_count >= MIN_SHX_FOR_DRAWING
                or len(self.items) >= MIN_ITEMS_FOR_DRAWING
            )
        return len(self.items) >= MIN_ITEMS_FOR_TEXT_LAYER

    @property
    def is_landscape(self) -> bool:
        return self.width >= self.height

    @property
    def looks_like_drawing(self) -> bool:
        """A plotted drawing sheet rather than a report or spec page.

        Used to decide whether an unreadable page is a problem (a drawing we
        cannot check) or simply not our business (a specification PDF that
        happened to be in the folder).  Covers both plotted vector drawings
        and full-page raster scans of older as-builts.

        Gathering drawings are A1 frames plotted to A3 (1191pt) or larger. The
        1000pt floor keeps landscape A4 figures inside specifications (842pt)
        from being treated as drawing sheets.
        """
        return (
            self.is_landscape
            and self.width >= 1000
            and (self.vector_count >= 400 or self.raster_coverage >= 0.5)
        )

    @property
    def is_scan(self) -> bool:
        return self.raster_coverage >= 0.5 and self.vector_count < 400

    # -- querying -------------------------------------------------------

    def region(
        self,
        x0: float = 0.0,
        y0: float = 0.0,
        x1: float | None = None,
        y1: float | None = None,
        *,
        frac: bool = False,
    ) -> list[TextItem]:
        """Items whose centre falls inside a box.

        With ``frac=True`` the box is given as fractions of page width/height,
        which keeps region rules independent of sheet size (A1 vs A3).
        """
        x1 = self.width if x1 is None else x1
        y1 = self.height if y1 is None else y1
        if frac:
            x0, x1 = x0 * self.width, x1 * self.width
            y0, y1 = y0 * self.height, y1 * self.height
        return [i for i in self.items if x0 <= i.cx <= x1 and y0 <= i.cy <= y1]

    def find_label(
        self,
        text: str,
        *,
        within: list[TextItem] | None = None,
        exact: bool = True,
    ) -> TextItem | None:
        """Locate a fixed template label (e.g. "DRAWING NO.").

        Returns the single best match, or None.  Where a label appears more
        than once on the sheet the caller must narrow the search with
        ``within`` -- "DRAWN" and "DRAWING NO." each appear in both the title
        block and a bottom table.
        """
        pool = self.items if within is None else within
        wanted = _norm(text)
        matches = [i for i in pool if _norm(i.text) == wanted]
        if not matches and not exact:
            matches = [i for i in pool if wanted in _norm(i.text)]
        if not matches:
            return None
        # Shortest text wins -- guards against a note that merely contains the
        # label being preferred over the label itself.
        return min(matches, key=lambda i: len(i.text))

    def near(
        self,
        anchor: TextItem,
        *,
        dx: tuple[float, float],
        dy: tuple[float, float],
        min_height: float = 0.0,
        max_height: float = 1e9,
    ) -> list[TextItem]:
        """Items in a window defined relative to an anchor label's top-left.

        Windows are anchor-relative rather than absolute so that the small
        (1-2pt) drift between drawings from different DP packages does not
        break field extraction.
        """
        out = []
        for i in self.items:
            if i is anchor:
                continue
            if not (anchor.x0 + dx[0] <= i.x0 <= anchor.x0 + dx[1]):
                continue
            if not (anchor.y0 + dy[0] <= i.y0 <= anchor.y0 + dy[1]):
                continue
            if not (min_height <= i.height <= max_height):
                continue
            out.append(i)
        return sorted(out, key=lambda i: (i.y0, i.x0))

    def text_blob(self, items: list[TextItem] | None = None) -> str:
        pool = self.items if items is None else items
        return "\n".join(i.text for i in pool)


def _norm(s: str) -> str:
    """Normalise for label matching: upper, collapse whitespace, drop dots."""
    return re.sub(r"[\s.]+", " ", s.upper()).strip().rstrip(".")


def dedupe_shx_repeat(text: str) -> str:
    """Undo the leading-line duplication in captured SHX note blocks.

    The capture writes multi-line MTEXT as one annotation and repeats the first
    physical line of each paragraph, e.g. "1. FOO BAR FOO BAR BAZ".  Collapse
    any immediately repeated run of >=3 words.
    """
    # The capture often glues the repeat straight on: "...BYPASS.HOT TAP...".
    text = re.sub(r"\.([A-Z])", r". \1", text)

    words = text.split()
    out: list[str] = []
    i = 0
    while i < len(words):
        matched = False
        # Longest repeat first so "A B C A B C" collapses whole, not in pieces.
        # Down to a single word, because a one-word note ("DELETED.") comes
        # back from the capture as "DELETED. DELETED." -- but only for real
        # words, so a genuine repeated size or spec code ("125 125") survives.
        for n in range(min(40, (len(words) - i) // 2), 0, -1):
            if words[i : i + n] != words[i + n : i + 2 * n]:
                continue
            if n == 1 and not re.fullmatch(r"[A-Za-z]{4,}[.,;:]?", words[i]):
                continue
            out.extend(words[i : i + n])
            i += 2 * n
            matched = True
            break
        if not matched:
            out.append(words[i])
            i += 1
    return " ".join(out)


def merge_words_into_strings(
    words: list[TextItem],
    gap_factor: float = 0.55,
    max_gap: float = 9.0,
    y_tolerance: float = 2.0,
) -> list[TextItem]:
    """Join OCR word tokens back into the strings they came from.

    The SHX capture hands over whole strings ("DRAWING NO."), but an OCR layer
    hands over one item per word. Every label-driven check depends on whole
    strings, so words sitting on the same baseline and close enough together
    are joined back up.

    The allowed gap is deliberately tight -- about half a character height, and
    never more than ``max_gap``. A drawing frame puts unrelated text on the
    same baseline in adjacent columns (the drawing number and the revision, the
    copyright note and the sign-off labels), and a generous gap merges them
    into nonsense.
    """
    if not words:
        return []

    rows: list[list[TextItem]] = []
    for word in sorted(words, key=lambda i: (round(i.y0 / y_tolerance), i.x0)):
        for row in rows:
            if abs(row[-1].y0 - word.y0) <= y_tolerance:
                row.append(word)
                break
        else:
            rows.append([word])

    merged: list[TextItem] = []
    for row in rows:
        row.sort(key=lambda i: i.x0)
        run = [row[0]]
        for word in row[1:]:
            gap = word.x0 - run[-1].x1
            allowed = min(
                gap_factor * max(run[-1].height, word.height, 4.0), max_gap
            )
            if -1.0 <= gap <= allowed:
                run.append(word)
            else:
                merged.append(_join(run))
                run = [word]
        merged.append(_join(run))
    return merged


def _join(run: list[TextItem]) -> TextItem:
    if len(run) == 1:
        return run[0]
    return TextItem(
        " ".join(i.text for i in run),
        min(i.x0 for i in run), min(i.y0 for i in run),
        max(i.x1 for i in run), max(i.y1 for i in run),
        run[0].source,
    )


# The PDF was written by AutoCAD's own plot driver, so any embedded text in it
# came from the drawing rather than from a recognition pass over an image.
CAD_PRODUCER = re.compile(r"pdfplot|autocad", re.IGNORECASE)


def _plotted_by_cad(meta: dict) -> bool:
    return bool(
        CAD_PRODUCER.search(meta.get("producer") or "")
        or CAD_PRODUCER.search(meta.get("creator") or "")
    )


def _raster_coverage(page) -> float:
    """Fraction of the page covered by its single largest placed image."""
    area = page.rect.get_area()
    if not area:
        return 0.0
    try:
        infos = page.get_image_info()
    except Exception:
        return 0.0
    return max(
        (fitz.Rect(i["bbox"]).get_area() / area for i in infos), default=0.0
    )


def load_sheets(pdf_path: str | Path) -> list[Sheet]:
    """Read every page of a PDF into a Sheet with its positioned text layer."""
    pdf_path = Path(pdf_path)
    doc = fitz.open(pdf_path)
    sheets: list[Sheet] = []
    try:
        meta = dict(doc.metadata or {})
        for index, page in enumerate(doc):
            items: list[TextItem] = []

            # SHX strings live in annotations, in *unrotated* page space.
            rot = page.rotation_matrix
            for annot in page.annots() or []:
                content = (annot.info or {}).get("content") or ""
                content = content.strip()
                if not content:
                    continue
                r = fitz.Rect(annot.rect) * rot
                items.append(
                    TextItem(content, r.x0, r.y0, r.x1, r.y1, "shx")
                )

            # Real embedded text is already reported in displayed space.
            words = []
            for x0, y0, x1, y1, word, *_ in page.get_text("words"):
                word = word.strip()
                if word:
                    words.append(TextItem(word, x0, y0, x1, y1, "text"))

            # Either way, embedded text arrives one item per word and has to be
            # joined back into strings before the label-driven checks can work.
            word_layer = not items and len(words) >= MIN_ITEMS_FOR_DRAWING
            if word_layer:
                words = merge_words_into_strings(words)
            items.extend(words)

            # A word layer is only untrustworthy if something *recognised* it.
            # Plotted straight from AutoCAD it is the drawing's own text -- the
            # drafter simply used a TrueType font instead of an SHX one, which
            # is better than the SHX capture, not worse. Added afterwards by
            # Bluebeam, Acrobat or a scanner, it is a guess at the pixels.
            is_ocr = word_layer and not _plotted_by_cad(meta)

            sheets.append(
                Sheet(
                    pdf_path=pdf_path,
                    page_index=index,
                    page_count=doc.page_count,
                    width=page.rect.width,
                    height=page.rect.height,
                    items=items,
                    meta=meta,
                    vector_count=len(page.get_drawings()),
                    raster_coverage=_raster_coverage(page),
                    is_ocr=is_ocr,
                )
            )
    finally:
        doc.close()
    return sheets
