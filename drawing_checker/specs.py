"""Origin HDPE pipe specifications, and the checks that use them.

The spec sheets in ``reference/`` are mostly scans with no text layer (P128,
P151 and P153 have none at all; P126 carries a poor OCR layer that garbles
"CATALOGUE MOULDED FITTINGS" into "CAIALUGUE MOULUEDFILLINGS"). Running OCR at
check time would put unreliable data straight into a FAIL, so the tables below
are **transcribed by hand from the drawings and are the authority**.

Each entry records the source file it came from, including that file's
revision, so ``check_spec_sources`` can warn when a spec in ``reference/`` has
been superseded and the transcription needs revisiting.

To add a spec: read its sheet, add an entry, and note the source filename.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

GAS = "gas"
WATER = "water"


@dataclass(frozen=True)
class ValveEntry:
    code: str        # VB16, VB17E, VF18, VG08 ...
    description: str
    min_size: int    # OD or DN, inclusive
    max_size: int


@dataclass(frozen=True)
class PipeSpec:
    code: str
    material: str
    sdr: str
    service: str          # GAS or WATER
    service_text: str
    min_size: int
    max_size: int
    special_order_above: int | None
    source: str           # the reference PDF this was transcribed from
    revision: str
    valves: tuple[ValveEntry, ...] = field(default_factory=tuple)

    def valve(self, code: str) -> ValveEntry | None:
        for entry in self.valves:
            if entry.code == code.upper():
                return entry
        return None


SPECS: dict[str, PipeSpec] = {
    "P126": PipeSpec(
        code="P126", material="PE100", sdr="13.6", service=WATER,
        service_text="PRODUCED / POTABLE / PERMEATE / SALINE WATER SERVICE",
        min_size=20, max_size=1200, special_order_above=None,
        source="A-1000-50-DC-P126_10_IFC.pdf", revision="10",
        valves=(
            ValveEntry("VB17", "VALVE, BALL, VB17", 63, 400),
            ValveEntry("VB17E", "VALVE, BALL, VB17E", 63, 400),
            ValveEntry("VF18", "VALVE, BUTTERFLY, VF18", 50, 900),
        ),
    ),
    "P128": PipeSpec(
        code="P128", material="PE100", sdr="11", service=WATER,
        service_text="PRODUCED / POTABLE / PERMEATE / SALINE WATER SERVICE",
        min_size=20, max_size=1200, special_order_above=None,
        source="A-1000-50-DC-P128_10_IFC.pdf", revision="10",
        valves=(
            ValveEntry("VB17", "VALVE, BALL, VB17", 63, 400),
            ValveEntry("VB17E", "VALVE, BALL, VB17E", 63, 400),
            ValveEntry("VF18", "VALVE, BUTTERFLY, VF18", 50, 900),
        ),
    ),
    "P150": PipeSpec(
        code="P150", material="PE100", sdr="7.4", service=WATER,
        service_text="PRODUCED / POTABLE / PERMEATE / SALINE WATER SERVICE",
        min_size=20, max_size=450, special_order_above=450,
        source="A-1000-50-DC-P150_2_IFC.pdf", revision="2",
        valves=(
            # The sheet lists VB32 at OD125 only, plus a note that alternatives
            # are to be SP items specific to the project.
            ValveEntry("VB32", "VALVE, BALL, VB32", 125, 125),
        ),
    ),
    # P130 and P157 have no spec sheet in reference/. Their SDR and service
    # come from ARN10-P-CALC-0029 (the MAOP review), and P130's valve from the
    # client's own checkprint comment: "VB32 ... is SDR7.4 and normally
    # installed in P130 lines with EF couplers". Size ranges are unknown, so
    # they are left wide open rather than guessed -- VLV-02 will not flag a
    # size on these, which is the honest behaviour.
    "P130": PipeSpec(
        code="P130", material="PE100", sdr="9", service=WATER,
        service_text="WATER SERVICE",
        min_size=0, max_size=10_000, special_order_above=None,
        source="ARN10-P-CALC-0029 Rev A (MAOP review) + client checkprint",
        revision="A",
        valves=(ValveEntry("VB32", "VALVE, BALL, VB32", 0, 10_000),),
    ),
    "P157": PipeSpec(
        code="P157", material="PE100", sdr="11", service=GAS,
        service_text="NATURAL GAS SERVICE",
        min_size=0, max_size=10_000, special_order_above=None,
        source="ARN10-P-CALC-0029 Rev A (MAOP review)", revision="A",
        valves=(),  # no valve list available; VLV-03 reports NO DATA
    ),
    "P151": PipeSpec(
        code="P151", material="PE100", sdr="21", service=GAS,
        service_text="NATURAL GAS SERVICE",
        min_size=32, max_size=1200, special_order_above=None,
        source="A-1000-50-DC-P151_1_IFC.pdf", revision="1",
        valves=(
            ValveEntry("VB16", "VALVE, BALL, VB16", 63, 630),
            ValveEntry("VB16E", "VALVE, BALL, VB16E", 63, 630),
            ValveEntry("VF18", "VALVE, BUTTERFLY, VF18", 50, 900),
            ValveEntry("VG08", "VALVE, GATE, VG08", 400, 900),
        ),
    ),
    "P153": PipeSpec(
        code="P153", material="PE100", sdr="17", service=GAS,
        service_text="NATURAL GAS SERVICE",
        min_size=25, max_size=1200, special_order_above=None,
        source="A-1000-50-DC-P153_1_IFC.pdf", revision="1",
        valves=(
            ValveEntry("VB16", "VALVE, BALL, VB16", 63, 630),
            ValveEntry("VB16E", "VALVE, BALL, VB16E", 63, 630),
            ValveEntry("VF18", "VALVE, BUTTERFLY, VF18", 50, 900),
            ValveEntry("VG08", "VALVE, GATE, VG08", 400, 900),
        ),
    ),
}

# Service letters used in line numbers and valve tags.
SERVICE_OF_CODE = {"RG": GAS, "PG": GAS, "RW": WATER, "PW": WATER}
SERVICE_NAME = {"RG": "raw gas", "PG": "product gas",
                "RW": "raw water", "PW": "product water"}

# Which service each valve code belongs to, derived from the specs above. A
# valve appearing only in gas specs is a gas valve, and vice versa.
def _service_of_valve() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for spec in SPECS.values():
        for entry in spec.valves:
            out.setdefault(entry.code, set()).add(spec.service)
    return out


VALVE_SERVICE = _service_of_valve()

# Valve tag as drawn: optional service prefix, size, valve type and code.
# e.g. RG125VB16, RW400VB17, 125VB17 (older Origin sheets omit the service).
VALVE_TAG = re.compile(
    r"^(?P<svc>RG|RW|PG|PW)?(?P<size>\d{2,4})(?P<kind>VB|VF|VG)(?P<code>\d{1,3}[A-Z]?)$"
)

# The SHX capture sometimes splits a valve tag into two annotations: the
# service prefix lands in its own item ("RG") butted against the rest
# ("630VB16"). Measured on the DP094 set the prefix sits within ~2pt of the
# tag's leading edge -- left of a horizontal tag, off either end of a vertical
# one. Without reuniting them, every valve on such a sheet reads prefix-less
# and VLV-01 reports "no service" against tags that plainly carry one.
SERVICE_PREFIXES = {"RG", "RW", "PG", "PW"}


@dataclass(frozen=True)
class ValveHit:
    """A valve tag as drawn, with any split-off service prefix reunited."""

    text: str   # full tag, e.g. RG630VB16
    svc: str    # RG / RW / PG / PW, or "" when the drawing carries none
    size: str
    code: str   # kind + code, e.g. VB16
    item: object

    @property
    def rect(self) -> tuple[float, float, float, float]:
        i = self.item
        return (i.x0, i.y0, i.x1, i.y1)


def _adjacent_service(item, prefix_items) -> str:
    """The service prefix item butted against a prefix-less valve tag, if any."""
    width, height = item.x1 - item.x0, item.y1 - item.y0
    candidates = []
    for p in prefix_items:
        if width >= height:
            # Horizontal tag: prefix immediately to the left of the size.
            if abs(p.cy - item.cy) <= height and item.x0 - 14 <= p.cx <= item.x0 + 3:
                candidates.append(p)
        else:
            # Vertical tag: prefix butted onto either end.
            if item.x0 - 3 <= p.cx <= item.x1 + 3 and (
                item.y0 - 14 <= p.cy <= item.y0 + 3
                or item.y1 - 3 <= p.cy <= item.y1 + 14
            ):
                candidates.append(p)
    if not candidates:
        return ""
    nearest = min(
        candidates,
        key=lambda p: (p.cx - item.cx) ** 2 + (p.cy - item.cy) ** 2,
    )
    return nearest.text.strip().upper()


def find_valves(items) -> list[ValveHit]:
    prefix_items = [i for i in items if i.text.strip().upper() in SERVICE_PREFIXES]
    hits = []
    for item in items:
        m = VALVE_TAG.match(item.text.strip())
        if not m:
            continue
        svc = (m["svc"] or _adjacent_service(item, prefix_items)).upper()
        hits.append(ValveHit(
            text=svc + m.group(0) if not m["svc"] else m.group(0),
            svc=svc,
            size=m["size"],
            code=(m["kind"] + m["code"]).upper(),
            item=item,
        ))
    return hits


def spec_source_warnings(reference_dir: Path) -> list[str]:
    """Warn when a spec PDF present on disk is not the revision transcribed.

    Filenames carry the revision, e.g. ``A-1000-50-DC-P151_1_IFC.pdf`` is Rev 1.
    """
    warnings: list[str] = []
    if not reference_dir.is_dir():
        return warnings
    on_disk = {p.name: p for p in reference_dir.glob("A-1000-50-DC-*.pdf")}
    for spec in SPECS.values():
        if spec.source in on_disk:
            continue
        stem = spec.source.split("_")[0]  # A-1000-50-DC-P151
        others = [name for name in on_disk if name.startswith(stem + "_")]
        if others:
            warnings.append(
                f"{spec.code}: transcribed from {spec.source} (Rev {spec.revision}) "
                f"but {reference_dir.name}/ now holds {', '.join(others)} - "
                "re-read the spec and update drawing_checker/specs.py."
            )
    return warnings


def run_spec_checks(sheet, tb, add) -> None:
    """Line-number and valve checks against the pipe specifications."""
    from .checks import FAIL, NA, NO_DATA, PASS, REVIEW
    from .content_checks import LINE_NUMBER, LINE_NUMBER_START, body_items

    items = body_items(sheet)

    # -- line number vs its spec ------------------------------------------
    lines = []
    for item in items:
        for candidate in LINE_NUMBER_START.findall(item.text):
            if m := LINE_NUMBER.fullmatch(candidate):
                lines.append((m, item))

    if not lines:
        add("SPEC-01", "Piping Spec", "Line service matches its pipe specification",
            NA, "No line numbers on this sheet.", "Rows 24, 54")
        add("SPEC-02", "Piping Spec", "Line size within the specification range",
            NA, "No line numbers on this sheet.", "Rows 24, 54")
    else:
        wrong_service, unknown, out_of_range, special = [], [], [], []
        for m, item in lines:
            spec = SPECS.get(m["spec"].upper())
            service = SERVICE_OF_CODE.get(m["service"])
            if not spec:
                unknown.append(f"{m.group(0)} (spec {m['spec']})")
                continue
            if service and service != spec.service:
                wrong_service.append(
                    f"{m.group(0)}: {m['service']} is {SERVICE_NAME[m['service']]} "
                    f"but {spec.code} is a {spec.service} spec"
                )
            size = int(m["size"])
            if spec.special_order_above and size > spec.special_order_above:
                special.append(f"{m.group(0)} (OD{size} > {spec.special_order_above})")
            elif not spec.min_size <= size <= spec.max_size:
                out_of_range.append(
                    f"{m.group(0)}: OD{size} outside {spec.code} range "
                    f"{spec.min_size}-{spec.max_size}"
                )

        known = len(lines) - len(unknown)
        if wrong_service:
            add("SPEC-01", "Piping Spec", "Line service matches its pipe specification",
                FAIL, "; ".join(wrong_service), "Rows 24, 54")
        elif not known:
            add("SPEC-01", "Piping Spec", "Line service matches its pipe specification",
                NO_DATA,
                "None of the specs used on this sheet have been transcribed: "
                + ", ".join(sorted({u.split("spec ")[1] for u in unknown})),
                "Rows 24, 54")
        else:
            note = (f" ({len(unknown)} line(s) use a spec not supplied: "
                    f"{', '.join(sorted({u.split('spec ')[1].rstrip(')') for u in unknown}))})"
                    if unknown else "")
            add("SPEC-01", "Piping Spec", "Line service matches its pipe specification",
                PASS, f"{known} line(s) checked, service matches the spec{note}.",
                "Rows 24, 54")

        if out_of_range:
            add("SPEC-02", "Piping Spec", "Line size within the specification range",
                FAIL, "; ".join(out_of_range), "Rows 24, 54")
        elif special:
            add("SPEC-02", "Piping Spec", "Line size within the specification range",
                REVIEW,
                "Above the standard size range, so a special order per the spec: "
                + "; ".join(special), "Rows 24, 54")
        elif known:
            add("SPEC-02", "Piping Spec", "Line size within the specification range",
                PASS, f"{known} line(s) within their spec's size range.", "Rows 24, 54")
        else:
            add("SPEC-02", "Piping Spec", "Line size within the specification range",
                NO_DATA, "No transcribed spec applies to these lines.", "Rows 24, 54")

    # -- valves ------------------------------------------------------------
    valves = find_valves(items)

    if not valves:
        for code, title in [
            ("VLV-01", "Valve type suits the service it is on"),
            ("VLV-02", "Valve size within its permitted range"),
            ("VLV-03", "Valve permitted by the specification of its line"),
        ]:
            add(code, "Piping Spec", title, NA, "No valve tags on this sheet.",
                "Rows 62, 63")
        return

    _check_valve_service(valves, add)
    _check_valve_sizes(valves, add)
    _check_valve_against_line(valves, lines, add)


def _check_valve_service(valves, add) -> None:
    """A gas valve on a raw-water tag (or vice versa) is objectively wrong."""
    from .checks import FAIL, NO_DATA, PASS

    problems, anchors, unknown = [], [], []
    checked = 0
    for v in valves:
        services = VALVE_SERVICE.get(v.code)
        tag_service = SERVICE_OF_CODE.get(v.svc)
        if not services:
            unknown.append(v.code)
            continue
        if not tag_service:
            continue  # older tags carry no service prefix
        checked += 1
        if tag_service not in services:
            problems.append(
                f"{v.text}: {v.svc} is {SERVICE_NAME[v.svc]} but {v.code} "
                f"is a {'/'.join(sorted(services))} valve"
            )
            anchors.append(v.rect)

    if problems:
        add("VLV-01", "Piping Spec", "Valve type suits the service it is on", FAIL,
            "; ".join(problems)
            + ". Check the two valve codes have not been transposed.",
            "Rows 62, 63", anchors=anchors)
    elif checked:
        add("VLV-01", "Piping Spec", "Valve type suits the service it is on", PASS,
            f"{checked} valve tag(s), service prefix agrees with the valve type."
            + (f" Not in any supplied spec: {', '.join(sorted(set(unknown)))}."
               if unknown else ""),
            "Rows 62, 63")
    else:
        add("VLV-01", "Piping Spec", "Valve type suits the service it is on", NO_DATA,
            "Valve tags carry no service prefix on this sheet, so the valve type "
            "cannot be checked against the service.", "Rows 62, 63")


def _check_valve_sizes(valves, add) -> None:
    from .checks import FAIL, NO_DATA, PASS

    problems, anchors, unknown = [], [], []
    checked = 0
    for v in valves:
        entries = [
            spec.valve(v.code) for spec in SPECS.values() if spec.valve(v.code)
        ]
        if not entries:
            unknown.append(v.code)
            continue
        checked += 1
        size = int(v.size)
        # Permitted if any spec listing this valve allows the size.
        if not any(e.min_size <= size <= e.max_size for e in entries):
            allowed = ", ".join(
                sorted({f"{e.min_size}-{e.max_size}" for e in entries})
            )
            problems.append(f"{v.text}: OD{size} outside {v.code} range {allowed}")
            anchors.append(v.rect)

    if problems:
        add("VLV-02", "Piping Spec", "Valve size within its permitted range", FAIL,
            "; ".join(problems), "Rows 62, 63", anchors=anchors)
    elif checked:
        add("VLV-02", "Piping Spec", "Valve size within its permitted range", PASS,
            f"{checked} valve(s) within the size range their spec allows.",
            "Rows 62, 63")
    else:
        add("VLV-02", "Piping Spec", "Valve size within its permitted range", NO_DATA,
            f"No supplied spec lists: {', '.join(sorted(set(unknown)))}.",
            "Rows 62, 63")


def _check_valve_against_line(valves, lines, add):
    """Match each valve to its line, then check the spec permits that valve.

    The valve is matched to the nearest line label of the same size and
    service, which is far more reliable than proximity alone -- a wellsite tie
    -in has gas and water lines running within a few millimetres of each other.
    """
    from .checks import FAIL, NO_DATA, PASS, REVIEW

    problems, ambiguous, not_supplied, unmatched = [], [], [], []
    anchors = []
    checked = 0
    for v in valves:
        # Older Origin sheets tag valves without a service prefix. The valve
        # code itself implies the service (VB16 gas, VB17 water), and using it
        # is essential -- gas and water lines of the same size run alongside
        # each other, so matching on size alone picks the wrong line.
        wanted = SERVICE_OF_CODE.get(v.svc)
        if not wanted:
            services = VALVE_SERVICE.get(v.code) or set()
            wanted = next(iter(services)) if len(services) == 1 else None
        candidates = [
            (lm, li) for lm, li in lines
            if lm["size"] == v.size
            and (wanted is None or SERVICE_OF_CODE.get(lm["service"]) == wanted)
        ]
        if not candidates:
            unmatched.append(v.text)
            continue
        def distance(c):
            return (c[1].cx - v.item.cx) ** 2 + (c[1].cy - v.item.cy) ** 2

        ranked = sorted(candidates, key=distance)
        lm, li = ranked[0]
        # Wellsites run several lines of the same size and service to different
        # specs, and a tag's position only tells us where the *label* is, not
        # which line it belongs to. Whenever more than one spec is plausible,
        # the association is a guess and the finding drops to REVIEW.
        rivals = {c[0]["spec"].upper() for c in ranked[1:]} - {lm["spec"].upper()}

        spec = SPECS.get(lm["spec"].upper())
        if not spec or not spec.valves:
            # No spec sheet, or one known only from the MAOP review and so
            # carrying no valve list. Either way there is nothing to check
            # against -- saying "permits none" would be a false FAIL.
            not_supplied.append(f"{v.text} on {lm.group(0)} (spec {lm['spec']})")
            continue
        checked += 1
        if not spec.valve(v.code):
            allowed = ", ".join(e.code for e in spec.valves) or "none"
            message = (
                f"{v.text} sits on {lm.group(0)}, but {spec.code} "
                f"({spec.material} SDR {spec.sdr}, {spec.service}) permits {allowed}"
            )
            anchors.append(v.rect)
            if rivals:
                ambiguous.append(
                    message
                    + f" - though {', '.join(sorted(rivals))} line(s) of the same "
                      "size run just as close, so confirm which line it is on"
                )
            else:
                problems.append(message)

    detail_tail = ""
    if not_supplied:
        detail_tail += (f" Not checked, spec not supplied: "
                        f"{'; '.join(sorted(set(not_supplied))[:4])}.")
    if unmatched:
        detail_tail += (f" No line of matching size/service found for: "
                        f"{', '.join(sorted(set(unmatched))[:6])}.")

    if problems:
        add("VLV-03", "Piping Spec", "Valve permitted by the specification of its line",
            FAIL, "; ".join(problems + ambiguous) + "." + detail_tail, "Rows 62, 63",
            anchors=anchors)
    elif ambiguous:
        add("VLV-03", "Piping Spec", "Valve permitted by the specification of its line",
            REVIEW, "; ".join(ambiguous) + "." + detail_tail, "Rows 62, 63",
            anchors=anchors)
    elif checked:
        add("VLV-03", "Piping Spec", "Valve permitted by the specification of its line",
            PASS, f"{checked} valve(s) permitted by their line's spec." + detail_tail,
            "Rows 62, 63")
    else:
        add("VLV-03", "Piping Spec", "Valve permitted by the specification of its line",
            NO_DATA,
            "No valve could be tied to a line with a transcribed spec." + detail_tail,
            "Rows 62, 63")
