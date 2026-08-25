"""Read a DXF into the same Sheet shape the PDF reader produces.

The point of this module is that everything downstream stays put: a DXF-derived
Sheet carries its text as ``TextItem``s in the *plotted page's* coordinates, so
all the existing frame, body, spec, note and batch checks run unchanged. What
the DXF adds on top -- named blocks, layers, linetypes, pipe segments -- hangs
off ``Sheet.dxf`` for the checks that need real geometry.

Coordinates
-----------
Three spaces, chained:

    model  --viewport-->  paper  --plot scale + origin-->  mm  -->  PDF points

The viewport gives model->paper: its ``view_target_point`` is the model point
at the centre of the viewport, and ``height / view_height`` is the scale. The
layout's plot settings give paper->mm (``scale_numerator/denominator`` plus the
plot origin offset), and the paper size gives mm->points. Validated against the
plotted PDF: the title block's DOCUMENT attribute lands within 7pt of where the
text actually sits on the sheet.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf

from .extract import Sheet, TextItem

# A3 landscape in points, the size these drawings plot to.
DEFAULT_PAGE = (1191.0, 842.0)
POINTS_PER_MM = 72.0 / 25.4


@dataclass
class BlockRef:
    """A block insertion, in page coordinates, with its attributes."""

    name: str
    kind: str            # semantic kind from BLOCK_KINDS, or "" if unrecognised
    x: float
    y: float
    layer: str
    attributes: dict[str, str] = field(default_factory=dict)

    def attr(self, *names: str) -> str:
        for name in names:
            for tag, value in self.attributes.items():
                if tag.upper() == name.upper() and value.strip():
                    return value.strip()
        return ""


@dataclass
class Segment:
    """A straight run of pipe (or any line), in page coordinates."""

    layer: str
    linetype: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def length(self) -> float:
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)

    def ends(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return (self.x0, self.y0), (self.x1, self.y1)


@dataclass
class DxfModel:
    """What a DXF gives us that a PDF cannot."""

    layout: str = ""
    blocks: list[BlockRef] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    layer_linetypes: dict[str, str] = field(default_factory=dict)
    unrecognised_blocks: dict[str, int] = field(default_factory=dict)

    def of_kind(self, kind: str) -> list[BlockRef]:
        return [b for b in self.blocks if b.kind == kind]


# -- block naming -------------------------------------------------------------
#
# Three drafting lineages are in use across the IF439 set and they name the same
# symbol differently, e.g. a flow arrow is "tag_flowa", "Flow Arrow" or
# "PEF_STAGE-4_OVERALL$0$FLOW". Matching is on a normalised name, and anything
# unrecognised is counted and reported rather than silently ignored -- that
# report is how this table gets extended.

FLOW_ARROW = "flow_arrow"
SPEC_BREAK = "spec_break"
VALVE = "valve"
REDUCER = "reducer"
TIE_IN = "tie_in"
HPV = "hpv"
LPD = "lpd"
DIP = "dip"
CONTINUATION = "continuation"
PROPERTY_BOUNDARY = "property_boundary"
CROSSING = "crossing"
WELL = "well"
BLIND = "blind"

BLOCK_KINDS: dict[str, list[str]] = {
    FLOW_ARROW: ["tag_flowa", "flow arrow", "flow", "flowarrow"],
    SPEC_BREAK: ["spec", "reducer spec", "prop boundary spec l",
                 "prop boundary spec r", "tag_speitem"],
    VALVE: ["valve", "valve_ug", "valve_inspv", "valve_insp", "val_ball",
            "val_gate", "valve symbol ug", "valve symbol ag", "valve ball",
            "valve gate", "valve accessible", "valve below ground",
            "valve_ag", "in_vflan"],
    REDUCER: ["in_concredv", "in_concredvatt", "reducer"],
    TIE_IN: ["tip", "tag_tip"],
    HPV: ["hpv"],
    LPD: ["lpd", "lpd-m", "lpd-a"],
    DIP: ["dip", "dip-m", "dip-a"],
    CONTINUATION: ["cont flag", "flag", "cont_tag", "cfm"],
    PROPERTY_BOUNDARY: ["prop boundary"],
    CROSSING: ["water_x", "crossing", "road_x", "hv_x", "powerline_x"],
    WELL: ["well_n0", "well_no", "wellhead", "well"],
    BLIND: ["in_blindflange", "blind", "in_spadeo", "in_capw"],
}

# Layers that carry process pipe, normalised (naming varies: "PROCESS - GAS",
# "PROCESS-GAS", "PROCESS1"...).
GAS_LAYERS = re.compile(r"^PROCESS\s*-?\s*GAS", re.IGNORECASE)
WATER_LAYERS = re.compile(r"^PROCESS\s*-?\s*WATER", re.IGNORECASE)
PIPE_LAYERS = re.compile(r"^PROCESS", re.IGNORECASE)


def normalise_block_name(name: str) -> str:
    """Strip the xref/layout prefix and tidy, e.g.
    ``PEF_STAGE-4_OVERALL$0$VALVE_UG`` -> ``valve_ug``."""
    name = name.split("$")[-1]
    return re.sub(r"[\s_]+", " ", name).strip().lower()


def classify_block(name: str) -> str:
    """Semantic kind for a block name, or "" if we do not recognise it."""
    plain = normalise_block_name(name)
    if not plain or plain.startswith("*"):      # anonymous block
        return ""
    best: tuple[str, str] | None = None
    for kind, patterns in BLOCK_KINDS.items():
        for pattern in patterns:
            # Normalise the pattern the same way as the name, so a table entry
            # written "val_ball" still matches a block called "Val_ball".
            pattern = re.sub(r"[\s_]+", " ", pattern).strip().lower()
            if pattern and pattern in plain:
                # Longest match wins, so "valve gate" beats "valve" and
                # "reducer spec" beats "reducer".
                if best is None or len(pattern) > len(best[1]):
                    best = (kind, pattern)
    return best[0] if best else ""


# -- coordinate transform -----------------------------------------------------


@dataclass
class PageTransform:
    """model/paper coordinates -> points on the plotted page."""

    page_width: float
    page_height: float
    paper_width_mm: float
    paper_height_mm: float
    plot_scale: float          # paper units -> mm
    origin_x_mm: float
    origin_y_mm: float
    model_scale: float = 1.0   # model units -> paper units
    model_dx: float = 0.0      # applied before scaling
    model_dy: float = 0.0
    viewport_x: float = 0.0    # viewport centre in paper units
    viewport_y: float = 0.0

    def paper_to_page(self, x: float, y: float) -> tuple[float, float]:
        mm_x = x * self.plot_scale + self.origin_x_mm
        mm_y = y * self.plot_scale + self.origin_y_mm
        return (
            mm_x * self.page_width / self.paper_width_mm,
            # DXF y runs up the sheet, PDF y runs down it.
            self.page_height - mm_y * self.page_height / self.paper_height_mm,
        )

    def model_to_page(self, x: float, y: float) -> tuple[float, float]:
        paper_x = self.viewport_x + (x - self.model_dx) * self.model_scale
        paper_y = self.viewport_y + (y - self.model_dy) * self.model_scale
        return self.paper_to_page(paper_x, paper_y)

    def length_to_page(self, model_length: float) -> float:
        return (
            model_length * self.model_scale * self.plot_scale
            * self.page_width / self.paper_width_mm
        )

    # -- inverses, for writing markup back into the drawing ---------------

    def page_to_paper(self, x: float, y: float) -> tuple[float, float]:
        mm_x = x * self.paper_width_mm / self.page_width
        mm_y = (self.page_height - y) * self.paper_height_mm / self.page_height
        return (
            (mm_x - self.origin_x_mm) / self.plot_scale,
            (mm_y - self.origin_y_mm) / self.plot_scale,
        )

    def page_to_model(self, x: float, y: float) -> tuple[float, float]:
        paper_x, paper_y = self.page_to_paper(x, y)
        scale = self.model_scale or 1.0
        return (
            (paper_x - self.viewport_x) / scale + self.model_dx,
            (paper_y - self.viewport_y) / scale + self.model_dy,
        )

    def page_length_to_paper(self, length: float) -> float:
        return length * self.paper_width_mm / self.page_width / self.plot_scale


def _build_transform(doc, layout) -> PageTransform:
    paper_w = layout.dxf.get("paper_width", 420.0) or 420.0
    paper_h = layout.dxf.get("paper_height", 297.0) or 297.0
    numerator = layout.dxf.get("scale_numerator", 1.0) or 1.0
    denominator = layout.dxf.get("scale_denominator", 1.0) or 1.0

    page_w, page_h = DEFAULT_PAGE
    if paper_w < paper_h:  # portrait sheet
        page_w, page_h = page_h, page_w

    transform = PageTransform(
        page_width=page_w,
        page_height=page_h,
        paper_width_mm=paper_w,
        paper_height_mm=paper_h,
        plot_scale=numerator / denominator,
        origin_x_mm=layout.dxf.get("plot_origin_x_offset", 0.0) or 0.0,
        origin_y_mm=layout.dxf.get("plot_origin_y_offset", 0.0) or 0.0,
    )

    # The real viewport is the one that is not the layout's own pseudo-viewport
    # (id 1). Take the largest, which is the drawing window.
    viewports = [v for v in layout.query("VIEWPORT") if v.dxf.id != 1]
    if viewports:
        vp = max(viewports, key=lambda v: v.dxf.width * v.dxf.height)
        view_height = vp.dxf.view_height or vp.dxf.height
        transform.model_scale = vp.dxf.height / view_height if view_height else 1.0
        target = vp.dxf.view_target_point
        centre = vp.dxf.view_center_point
        transform.model_dx = target.x + centre.x
        transform.model_dy = target.y + centre.y
        transform.viewport_x = vp.dxf.center.x
        transform.viewport_y = vp.dxf.center.y
    return transform


# -- extraction ---------------------------------------------------------------


def _text_items(space, transform: PageTransform, model: bool) -> list[TextItem]:
    """TEXT, MTEXT and block attributes, as positioned TextItems."""
    to_page = transform.model_to_page if model else transform.paper_to_page
    items: list[TextItem] = []

    def add(text: str, x: float, y: float, height: float) -> None:
        text = _clean(text)
        if not text:
            return
        px, py = to_page(x, y)
        h = max(
            transform.length_to_page(height) if model
            else height * transform.plot_scale * transform.page_width / transform.paper_width_mm,
            3.0,
        )
        # DXF gives an insertion point, not a box. Approximate the box so the
        # region and proximity rules downstream behave as they do for PDFs.
        # MTEXT arrives as one long string, so the width has to be clamped to
        # the sheet or a note block produces a box running off the page.
        width = min(
            0.62 * h * len(text),
            max(transform.page_width - px, h),
        )
        items.append(TextItem(text, px, py - h, px + width, py, "dxf"))

    for entity in space:
        kind = entity.dxftype()
        try:
            if kind == "TEXT":
                p = entity.dxf.insert
                add(entity.dxf.text, p.x, p.y, entity.dxf.height)
            elif kind == "MTEXT":
                p = entity.dxf.insert
                add(entity.text, p.x, p.y, entity.dxf.char_height)
            elif kind == "INSERT":
                for att in entity.attribs:
                    p = att.dxf.insert
                    add(att.dxf.text, p.x, p.y, att.dxf.height)
        except Exception:
            continue  # a malformed entity must not stop the read
    return items


MTEXT_CODES = re.compile(r"\\[A-Za-z][^;\\]*;|[{}]|\\P|\\~")


def _clean(text: str) -> str:
    """Strip MTEXT formatting codes and AutoCAD's %% escapes."""
    if not text:
        return ""
    text = MTEXT_CODES.sub(" ", text)
    text = text.replace("%%U", "").replace("%%u", "")
    text = text.replace("%%D", "deg").replace("%%C", "OD").replace("%%P", "+/-")
    return re.sub(r"\s+", " ", text).strip()


def _collect_model(doc, msp, layout, transform: PageTransform) -> DxfModel:
    model = DxfModel(layout=layout.name)
    for layer in doc.layers:
        model.layer_linetypes[layer.dxf.name] = layer.dxf.linetype

    unrecognised: dict[str, int] = {}
    for entity in msp:
        kind = entity.dxftype()
        try:
            if kind == "INSERT":
                classified = classify_block(entity.dxf.name)
                if not classified and not entity.dxf.name.startswith("*"):
                    unrecognised[entity.dxf.name] = unrecognised.get(entity.dxf.name, 0) + 1
                px, py = transform.model_to_page(entity.dxf.insert.x, entity.dxf.insert.y)
                model.blocks.append(BlockRef(
                    name=entity.dxf.name, kind=classified, x=px, y=py,
                    layer=entity.dxf.layer,
                    attributes={
                        a.dxf.tag: (a.dxf.text or "") for a in entity.attribs
                    },
                ))
            elif kind == "LINE":
                x0, y0 = transform.model_to_page(entity.dxf.start.x, entity.dxf.start.y)
                x1, y1 = transform.model_to_page(entity.dxf.end.x, entity.dxf.end.y)
                model.segments.append(Segment(
                    entity.dxf.layer, _linetype(entity, model), x0, y0, x1, y1
                ))
            elif kind == "LWPOLYLINE":
                points = [tuple(p) for p in entity.get_points("xy")]
                if entity.closed and len(points) > 2:
                    points.append(points[0])
                for a, b in zip(points, points[1:]):
                    x0, y0 = transform.model_to_page(*a)
                    x1, y1 = transform.model_to_page(*b)
                    model.segments.append(Segment(
                        entity.dxf.layer, _linetype(entity, model), x0, y0, x1, y1
                    ))
        except Exception:
            continue
    model.unrecognised_blocks = unrecognised
    return model


def _linetype(entity, model: DxfModel) -> str:
    linetype = entity.dxf.linetype
    if linetype and linetype.upper() != "BYLAYER":
        return linetype
    return model.layer_linetypes.get(entity.dxf.layer, "Continuous")


def printable_layouts(doc) -> list:
    """Layouts that represent a plotted sheet (everything but Model)."""
    return [doc.layout(name) for name in doc.layout_names() if name.lower() != "model"]


def load_dxf_sheets(path: str | Path) -> list[Sheet]:
    """One Sheet per printable layout in the DXF."""
    path = Path(path)
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()

    sheets: list[Sheet] = []
    layouts = printable_layouts(doc) or [doc.modelspace()]
    for index, layout in enumerate(layouts):
        transform = _build_transform(doc, layout)
        items = _text_items(layout, transform, model=False)
        items += _text_items(msp, transform, model=True)
        dxf_model = _collect_model(doc, msp, layout, transform)

        sheets.append(Sheet(
            pdf_path=path,
            page_index=index,
            page_count=len(layouts),
            width=transform.page_width,
            height=transform.page_height,
            items=items,
            meta={
                "producer": f"DXF {doc.dxfversion} ({doc.acad_release})",
                "creator": "DXF",
                "title": layout.name,
            },
            vector_count=len(dxf_model.segments),
            is_ocr=False,
            dxf=dxf_model,
            transform=transform,
        ))
    return sheets
