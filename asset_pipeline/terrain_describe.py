"""Describe the terrain in words (Layout tab's "Describe the terrain", `ap terrain roll --describe`): the text
becomes a TerrainCompose spec (schema.py) — a base, landforms with places, sizes, heights and steepness, and city
hints — that terrain_compose builds. It follows what was written instead of picking the nearest preset.

The local vision model's text mode does it when its server is already running (vlm.state(); it is never
started for this, and no paid provider is ever asked): its answer is validated against the schema and, if it
doesn't validate, asked once more with the error. Otherwise, or after a second bad answer, a keyword and phrase
parser does it, combining every landform it finds. describe() says which ran ("vlm" or "keywords"), what it
understood (the spec, as a readable line) and what it ignored.

Both keep the base of the terrain picked now (current(): plains stay inland and flat, an island an island, a
coast its side) unless the words name another or ask for a sea it hasn't; and a landform given no place goes
round the city, not under it (settle(): mountains, peaks and lakes beyond its edge on one or two sides away
from the sea, hills round its edges), so the city's core stays flat unless the words put something there.
"""
import json
import re

from pydantic import ValidationError

from . import terrain_compose
from .schema import TerrainCompose

# --- keywords -------------------------------------------------------------------------------------

# landforms: (pattern, type, defaults); a clause can hold several
FEATURES = [
    (r"\bvolcan\w*|\bcraters?\b|\bcaldera\b", "volcano", {"size": "large", "crater": True}),
    (r"\bmountain ranges?\b|\bmountains\b|\branges?\b|\bsierra\b", "ridge", {"size": "large", "height_m": 450.0}),
    (r"\bpeaks?\b|\bmountain\b|\bmount\b|\bsummit\b|\bpinnacle\b", "peak", {}),
    (r"\bridges?\b|\bescarpments?\b", "ridge", {}),
    (r"\bhills\b|\bhilly\b|\bknolls\b|\bhillocks\b|\bdowns\b", "hills", {}),
    (r"\bhill(?: ?tops?)?\b|\bknoll\b|\bhillock\b|\bmound\b", "hill", {}),
    (r"\bplateaus?\b|\bmesas?\b|\btablelands?\b|\btable mountain\b", "plateau", {}),
    (r"\bcliffs?\b|\bbluffs?\b|\bsea ?walls?\b", "cliffs", {"steepness": "steep"}),
    (r"\blakes?\b|\blagoons?\b|\bponds?\b|\breservoirs?\b|\btarns?\b|\bcrater lake\b", "lake", {}),
    (r"\bdeltas?\b", "river", {"count": 5}),
    (r"\brivers?\b|\bstreams?\b|\bcreeks?\b|\bchannels?\b|\bestuar\w*\b|\bwaterways?\b", "river", {}),
    (r"\bbays?\b|\bcoves?\b|\bgulfs?\b|\binlets?\b|\bharbou?rs?\b|\bfjords?\b", "bay", {}),
    (r"\bheadlands?\b|\bcapes?\b|\bpeninsulas?\b|\bpromontor\w*\b", "headland", {}),
    (r"\bvalleys?\b|\bgorges?\b|\bcanyons?\b|\bravines?\b|\bdales?\b", "valley", {}),
]
ISLAND = r"\bisland\w*|\bisles?\b|\batolls?\b|\barchipelago\b"
COAST = (r"\bcoast\w*|\bseaside\b|\bsea\b|\bocean\b|\bshores?\b|\bbeach\w*|\bbays?\b|\bharbou?rs?\b|\bports?\b"
         r"|\bcliffs?\b|\bdeltas?\b|\bestuar\w*|\bwaterfront\b")
# words that ask for the sea (cliffs alone don't: they stand inland too); coast and sea name the coast base
SEA = (r"\bcoast\w*|\bseaside\b|\bsea\b|\bocean\b|\bshores?\b|\bbeach\w*|\bbays?\b|\bcoves?\b|\bgulfs?\b"
       r"|\binlets?\b|\bfjords?\b|\bharbou?rs?\b|\bports?\b|\bdeltas?\b|\bestuar\w*|\bwaterfront\b")
COAST_BASE = r"\bcoast(?:line)?s?\b"
ON_HILL = (r"\b(?:on|atop|upon|on top of) (?:a |the |one )?(?:\w+ )?(?:hill|knoll|hillock|mound|rise)s?\b"
           r"|\bhill ?tops?\b")
CENTRE_WORDS = r"\b(?:centre|center|middle|heart|central)\b"
INLAND = r"\binland\b(?! *(?:hills?|ridges?|mountains?|peaks?))|\blandlocked\b|\bfar from the sea\b"
PLACES = [
    (r"\b(?:in|at) the (?:very )?(?:centre|center|middle|heart)\b|\bcentral\b|\bin the middle\b|\bat its heart\b", "centre"),
    (r"\bnorth[- ]?east\w*", "north-east"), (r"\bnorth[- ]?west\w*", "north-west"),
    (r"\bsouth[- ]?east\w*", "south-east"), (r"\bsouth[- ]?west\w*", "south-west"),
    (r"\bnorth\w*", "north"), (r"\bsouth\w*", "south"), (r"\beast\w*", "east"), (r"\bwest\w*", "west"),
    (r"\binland\b|\bbehind the city\b|\baway from the sea\b", "inland"),
    (r"\b(?:on|along|by) the (?:coast|sea|shore|water)\b|\bcoastal\b|\bseaside\b|\bwaterfront\b", "coast"),
    (r"\beverywhere\b|\ball (?:over|around|round)\b|\bthroughout\b", "everywhere"),
]
SIZES = [(r"\b(?:small|little|tiny|minor|low|modest)\b", "small"),
         (r"\b(?:large|big|huge|great|vast|giant|massive|tall|towering|high|wide|broad|enormous|lofty)\b", "large")]
STEEP = [(r"\b(?:steep|sheer|dramatic|jagged|craggy|precipitous|rugged)\b", "steep"),
         (r"\b(?:gentle|soft|rolling|smooth|shallow|low)\b", "gentle")]
COUNTS = {"two": 2, "twin": 2, "pair of": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "several": 3, "a few": 3, "few": 3, "some": 3, "many": 5, "numerous": 6, "multiple": 4, "lots of": 5}
GROUND = [(r"\b(?:flat|level|plain|plains|prairie|steppe|deltas?)\b", "flat"),
          (r"\b(?:rugged|rocky|craggy|broken|wild|jagged)\b", "rugged")]
HINTS = [
    (r"\bcircl\w*|\bencircl\w*|\bring\w*\b|\baround (?:it|the)\b|\bround (?:it|the)\b|\bsurround\w*", "ring"),
    (r"\b(?:along|on|by) the (?:coast|sea|shore|water(?:front)?)\b|\bharbou?r\b|\bport (?:city|town)\b|\bseaside (?:city|town)\b",
     "coast"),
    (r"\b(?:in|along|down) the valley\b|\bvalley floor\b", "valley"),
    (r"\b(?:along|on|by|beside|astride) the (?:river|channels?|banks?)\b|\briverside\b|\bboth banks\b", "river"),
    (r"\b(?:on|atop|on top of) the (?:plateau|mesa|table)\b", "plateau"),
]
CITY_WORDS = r"\b(?:city|cities|town|towns|settlements?|villages?|districts?|quarters?|houses?|streets?|buildings?)\b"
_NEG = re.compile(r"\b(?:no|not|without|never|nor|free of|lacking)\b"
                  r"((?:\s+(?!(?:and|but|with|yet|though|while|where|from|near|by|in|on|at|beside)\b)[a-z'-]+){1,4})")
_SPLIT = re.compile(r"[,;.\n]|\b(?:and|with|but|plus|then|while|where)\b")
_FILLER = {"a", "an", "the", "some", "of", "in", "on", "at", "by", "to", "and", "with", "also", "really", "very", "it",
           "its", "is", "are", "be", "there", "land", "terrain", "ground", "landscape", "place", "area", "please",
           "make", "want", "like", "near", "that", "this", "has", "have", "for", "between", "into", "from", "out",
           "over", "under", "across", "up", "down", "lots", "much", "more", "most", "side", "them", "their",
           "one", "all", "both"}
TRY = ("island", "volcano in the centre", "two ridges", "hills inland", "a lake", "a river", "delta", "cliffs on the west",
       "a bay", "plateau", "flat", "city along the river", "cities circling it")


def _first(pairs, text, default=None):
    best = None
    for pat, val in pairs:
        m = re.search(pat, text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), val)
    return best[1] if best else default


def _count(text: str, start: int) -> int | None:
    """A number written just before a match ("two ridges", "5 channels", "many river channels")."""
    head = text[max(0, start - 24):start]
    m = re.search(r"(\d+)\s+(?:[a-z-]+\s+)?$", head)
    if m:
        return max(1, min(8, int(m.group(1))))
    for w, n in sorted(COUNTS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{w}\s+(?:[a-z-]+\s+)?$", head):
            return n
    return None


def keywords(text: str, current: dict | None = None) -> dict:
    """{spec (TerrainCompose), understood [words], ignored [clauses], matched, inherited}: every landform the text
    names, each with the place, size, steepness and count its own clause gives; the base (current()'s when the
    text names none: inherited is where it came from), the sea's side and the city hints; settle() places what
    the text gave no place."""
    cur = current or CURRENT
    t = " " + re.sub(r"\s+", " ", text.lower()) + " "
    negated = " ".join(m.group(1) for m in _NEG.finditer(t))
    t = _NEG.sub(" , ", t)
    got: list[str] = []
    feats: list[dict] = []
    hints: list[dict] = []
    side = None
    pos = 0
    clauses = []
    for m in list(_SPLIT.finditer(t)) + [re.search(r"$", t)]:
        clauses.append(t[pos:m.start()])
        pos = m.end()
    ignored = []
    last_ring_target = None
    for cl in clauses:
        hit = False
        compass = _first([(p, v) for p, v in PLACES if v in terrain_compose.DIRS], cl)
        place = compass or _first(PLACES, cl)                 # "coastal cliffs on the west": west
        in_hint = [m.span() for pat, _ in HINTS for m in re.finditer(pat, cl)]
        size, steep = _first(SIZES, cl), _first(STEEP, cl)
        height = re.search(r"(\d{2,4})\s*(?:m|metres|meters)\b", cl)
        on_hill = re.search(ON_HILL, cl)                  # "a city on a hill (by the sea)": the hill is under it
        taken = []
        for pat, typ, dflt in FEATURES:
            for m in re.finditer(pat, cl):
                if any(a <= m.start() < b for a, b in taken) or any(a <= m.start() < b for a, b in in_hint):
                    continue
                taken.append((m.start(), m.end()))
                hit = True
                got.append(m.group(0).strip())
                f = {"type": typ, **dflt}
                if place:
                    f["at"] = place
                if on_hill and typ in ("hill", "hills"):
                    f["at"] = "centre"
                if size:
                    f["size"] = size
                if steep:
                    f["steepness"] = steep
                if height:
                    f["height_m"] = float(height.group(1))
                n = _count(cl, m.start())
                if n and typ in ("hills", "ridge", "river"):
                    f["count"] = n
                if typ == "river" and re.search(r"\bchannels?\b", m.group(0)) and "count" not in f:
                    f["count"] = 3
                if typ == "volcano" and re.search(r"\b(?:no|without) crater\b", text.lower()):
                    f["crater"] = False
                feats.append(f)
                break                                     # one of each type per clause
        if re.search(COAST, cl) and compass:
            side = side or compass
        for pat, kind in HINTS:
            if re.search(pat, cl) and (kind != "ring" or re.search(CITY_WORDS, cl) or "circl" in cl or "ring" in cl):
                hit = True
                if kind not in [h["kind"] for h in hints]:
                    hints.append({"kind": kind})
                got.append(re.search(pat, cl).group(0).strip())
        for pat in (ISLAND, INLAND, COAST, r"\bflat\b|\brugged\b|\brolling\b|\bhigh\b"):
            if re.search(pat, cl):
                hit = True
                got.append(re.search(pat, cl).group(0).strip())
        if re.search(CITY_WORDS, cl):
            hit = hit or bool(re.search(r"\b(?:in|on|along|around|circling|beside)\b", cl))
        words = [w.strip("'\"()") for w in cl.split()]
        if not hit and any(w and w not in _FILLER and not re.fullmatch(CITY_WORDS, f" {w} ".strip()) for w in words):
            ignored.append(" ".join(w for w in cl.split()))
    whole = t
    base, named = _base(whole, text, cur)
    side = side or (cur["side"] if not named or base == cur["base"] else None)
    # the base absorbs what it already is: a valley's own valley and two ridges, a plateau base's own table
    out = []
    for f in feats:
        if base == "valley" and f["type"] == "valley":
            continue
        if base == "valley" and f["type"] == "ridge" and f.get("count", 1) == 2:
            continue
        if base == "plateau" and f["type"] == "plateau":
            f["at"] = "centre"
        if f["type"] == "cliffs" and "at" not in f and side and base != "inland" and base != "plateau":
            f["at"] = side
        if f["type"] in ("bay", "headland") and "at" not in f:
            f["at"] = "coast"
        same = next((o for o in out if o["type"] == f["type"] and o.get("at") == f.get("at")), None)
        if same is not None:                              # the same landform named twice: one, the larger count
            if f.get("count", 1) > same.get("count", 1):
                same["count"] = f["count"]
            continue
        out.append(f)
    neg_types = {typ for pat, typ, _ in FEATURES if re.search(pat, negated)}
    out = [f for f in out if f["type"] not in neg_types]
    if neg_types:
        got += [f"no {x}" for x in sorted(neg_types)]
    for h in hints:                                       # a ring goes round the first landform it can ring
        if h["kind"] == "ring":
            idx = next((i for i, f in enumerate(out) if f["type"] in terrain_compose.RING_TYPES), None)
            if idx is None:
                hints = [x for x in hints if x is not h]
            else:
                h["feature"] = idx
    if "plateau" in [h["kind"] for h in hints] and base != "plateau" and not any(f["type"] == "plateau" for f in out):
        hints = [h for h in hints if h["kind"] != "plateau"]
    ground = _first(GROUND, whole, "rolling" if named else cur["ground"])
    if base == "plateau" and re.search(r"\bhigh\b", whole):
        for f in out:
            if f["type"] == "plateau" and "height_m" not in f:
                f.setdefault("size", "large")
        if not any(f["type"] == "plateau" for f in out):
            out.insert(0, {"type": "plateau", "at": "centre", "size": "large"})
            for h in hints:
                if h.get("feature") is not None:
                    h["feature"] += 1
    out, hints = settle(out[:12], hints[:4], base, side or "south", text)
    spec = TerrainCompose.model_validate({"base": base, "side": side or "south", "ground": ground,
                                         "features": out, "city": hints})
    seen, words = set(), []
    for w in got:
        if w not in seen:
            seen.add(w)
            words.append(w)
    return {"spec": spec, "understood": words, "ignored": ignored, "matched": bool(out or hints or named),
            "inherited": None if named else cur.get("from")}


# --- the base kept, and landforms placed round the city -------------------------------------------

def _base(whole: str, text: str, cur: dict) -> tuple[str, bool]:
    """(base, named): the base the words name, else the current one (named False); a sea is added only when
    the words ask for one."""
    if re.search(ISLAND, whole):
        return "island", True
    if re.search(r"\bvalleys?\b", whole) and not re.search(r"\b(?:no|without) valley", text.lower()):
        return "valley", True
    if re.search(r"\b(?:plateau|mesa|tableland)s?\b", whole) and not re.search(COAST, whole):
        return "plateau", True
    if re.search(INLAND, whole) and not re.search(COAST, whole):
        return "inland", True
    if re.search(COAST_BASE, whole):
        return "coast", True
    if re.search(SEA, whole):                             # a sea asked for: the current one's, else a coast
        return (cur["base"] if cur["base"] in ("island", "coast", "valley") else "coast"), True
    return cur["base"], False


CURRENT = {"base": "coast", "side": "south", "ground": "rolling", "from": None}    # no terrain picked: a coast
MOVABLE = ("ridge", "peak", "volcano", "hill", "plateau", "lake", "cliffs")      # beyond the city when not placed


def current(settings=None, sea_dir: float = -90.0, name: str | None = None) -> dict:
    """{base, side, ground, from} of a terrain (TerrainSettings; None: the project's own coast) as a
    described base: an island's island, an inland one's inland (a high one: plateau), a valley's valley, any
    other coast its side (sea_dir: the layout's); a described one its own spec's."""
    from .terrain_compose import DIRS
    side = min(DIRS, key=lambda k: abs(((DIRS[k] - sea_dir) + 180) % 360 - 180))
    label = (name or (settings.name if settings is not None else "the project's coast")).replace("-", " ")
    if settings is None:
        return {"base": "coast", "side": side, "ground": "rolling", "from": label}
    if settings.compose is not None:
        c = settings.compose
        return {"base": c.base, "side": c.side, "ground": c.ground, "from": "the described terrain"}
    ground = settings.base if settings.base in ("flat", "rugged") else "rolling"
    if settings.coast == "all":
        base = "island"
    elif settings.coast == "none":
        base = "plateau" if settings.plateau_m > 0 else "inland"
    elif settings.river and settings.mountains_m > 0:
        base = "valley"
    else:
        base = "coast"
    return {"base": base, "side": side, "ground": ground, "from": label}


def settle(feats: list[dict], hints: list[dict], base: str, side: str, text: str):
    """Places for the landforms the text gave none (no "at"): mountains, peaks, volcanoes, lakes, a lone hill,
    a plateau (not the base's own) and inland cliffs go beyond the city's edge, away from the sea, alternating
    over two neighbouring sides; hills go round its edges; a landform the city rings, cliffs on a coast and the
    rest (rivers, valleys) keep the centre. The city's core stays flat unless the text puts something there."""
    from .terrain_compose import DIRS, RING_TYPES, behind
    back = behind(TerrainCompose(side=side))
    sides = [back, min(DIRS, key=lambda k: abs(((DIRS[k] - DIRS[back] - 45) + 180) % 360 - 180))]
    ring = next((i for i, f in enumerate(feats) if f["type"] in RING_TYPES), None) \
        if any(h["kind"] == "ring" for h in hints) else None
    held = {i for i, f in enumerate(feats) if f["type"] == "plateau" and any(h["kind"] == "plateau" for h in hints)}
    k = 0
    for i, f in enumerate(feats):
        if "at" in f:
            continue
        if i == ring or i in held or f["type"] not in MOVABLE + ("hills",) or \
                (f["type"] == "cliffs" and base not in ("inland", "plateau")):
            f["at"] = "centre"
        elif f["type"] == "hills":
            f["at"] = "edges"
        else:
            f["at"], f["beyond"] = sides[k % 2], True
            k += 1
    return feats, hints


# --- the local model ------------------------------------------------------------------------------

SYSTEM = ("You turn a short description of a landscape into a terrain spec for a procedural terrain builder. Answer "
          "with JSON only, matching the schema. Use only the landforms the description asks for, one entry each "
          "(a count for several hills, ridges or river channels), with the place, size, height and steepness it "
          "gives. Places are relative to the city's centre. Put where the city should go in city. List in ignored "
          "any part of the description the spec can't show (an empty list when all of it fits).")


EXAMPLE = ("a rocky coast to the north with two small peaks inland and the town by the sea",
           {"base": "coast", "side": "north", "ground": "rugged",
            "features": [{"type": "peak", "at": "inland", "size": "small", "count": 1},
                         {"type": "peak", "at": "south", "size": "small", "count": 1}],
            "city": [{"kind": "coast"}]})


def _prompt(text: str, error: str | None = None, current: dict | None = None) -> str:
    schema = json.dumps(TerrainCompose.model_json_schema())
    ex_text, ex_spec = EXAMPLE
    cur = current or CURRENT
    now = f"base {cur['base']}" + (f", sea {cur['side']}" if cur["base"] in ("coast", "valley") else "") + \
        f", ground {cur['ground']}"
    out = (f"Spec schema (JSON Schema): {schema}\n\n"
           f"The terrain now: {now}.\n\n"
           "Rules:\n"
           "- base: keep the terrain's base, side and ground as they are now unless the description names another "
           "base (island, coast, sea, inland, valley, plateau). Add a sea only when it asks for one (sea, coast, "
           "shore, beach, island, bay, harbour, ocean): then base coast (an island or valley keeps its own sea); "
           "side is where the sea is.\n"
           "- Landforms the description gives no place go round the city, not under it: mountains, ridges, peaks, "
           "volcanoes and lakes at one or two compass sides away from the sea with beyond true; hills at edges. "
           "Use centre only when the description puts it there (in the centre, a city on a hill: one hill at the "
           "centre). Keep the city's centre flat otherwise.\n"
           "- A valley base already has its valley and the two ridges either side; don't list them (but a river valley "
           "still lists its river). A plateau base "
           "already is the raised table; list a plateau feature only to give its size or height.\n"
           "- height_m is metres above the ground around it, null unless the description gives a height. Typical: "
           "hills 40-120, ridge 150-500, peak 300-900, volcano 400-1200, plateau 80-400, cliffs 30-120. For a lake "
           "or a valley it is the depth: lake 3-20, valley 30-200.\n"
           "- A volcano with a crater sets crater true. A delta is one river with count 4-6 at the coast's centre. "
           "Several hills or channels are one feature with a count.\n"
           "- city: ring (round a landform; feature = its index), coast, valley, river, plateau; only what the "
           "description says about where the city goes.\n\n"
           f"Example: {ex_text}\nAnswer: {json.dumps({'spec': ex_spec, 'ignored': []})}\n\n"
           f"Description: {text.strip()}\n\nAnswer as {{\"spec\": <the spec>, \"ignored\": [<parts>]}}.")
    if error:
        out += f"\n\nYour last answer didn't validate: {error[:600]}. Answer again, valid this time."
    return out


ANSWER = {"type": "object", "properties": {"spec": {"type": "object"},
                                           "ignored": {"type": "array", "items": {"type": "string"}}},
          "required": ["spec"]}


def vlm_spec(text: str, ask=None, current: dict | None = None) -> dict | None:
    """The running local model's spec ({spec, ignored, tries}), asked once more if its first answer doesn't
    validate; None when no server is running, it fails, or neither answer validates. ask(system, prompt): the
    model call (tests pass a fake)."""
    from . import vlm
    if ask is None:
        st = vlm.state()
        if not st:
            return None

        def ask(system, prompt):
            from .jobs import using
            with using("gpu0", "vlm"):
                return vlm.client(st).json_text(system, prompt, [], ANSWER)
    error = None
    for k in range(2):
        try:
            got = json.loads(ask(SYSTEM, _prompt(text, error, current)))
            spec = TerrainCompose.model_validate(got.get("spec") if isinstance(got, dict) else None)
            lost = [str(x).strip() for x in (got.get("ignored") or []) if str(x).strip()][:6]
            return {"spec": spec, "ignored": lost, "tries": k + 1}
        except (ValidationError, ValueError, TypeError, AttributeError) as e:     # not JSON, or not the schema
            error = str(e)
        except Exception:                         # noqa: BLE001 - the server failed: the keywords decide
            return None
    return None


# --- both -----------------------------------------------------------------------------------------

def kept(spec: TerrainCompose, text: str, current: dict | None = None) -> tuple[TerrainCompose, str | None]:
    """The model's spec held to the same rules as the keywords: the current base, side and ground when the text
    names no base; landforms it put at the centre without the text saying so placed round the city (settle).
    Returns (spec, inherited)."""
    cur = current or CURRENT
    whole = " " + re.sub(r"\s+", " ", text.lower()) + " "
    _, named = _base(whole, text, cur)
    d = spec.model_dump(mode="json")
    if not named:
        d |= {"base": cur["base"], "side": cur["side"]}
        if not _first(GROUND, whole):
            d["ground"] = cur["ground"]
    if not re.search(CENTRE_WORDS, whole) and not re.search(ON_HILL, whole):
        for f in d["features"]:
            if f["at"] == "centre" and f["type"] in MOVABLE + ("hills",) and \
                    not (d["base"] == "plateau" and f["type"] == "plateau"):
                del f["at"]
        d["features"], d["city"] = settle(d["features"], d["city"], d["base"], d["side"], text)
    return TerrainCompose.model_validate(d), None if named else cur.get("from")


def describe(text: str, use_vlm: bool = True, ask=None, current: dict | None = None) -> dict:
    """{spec (TerrainCompose), line (readable), source: vlm | keywords, understood, ignored, matched, tries};
    current: the terrain picked now (current()), whose base a description that names none keeps."""
    if not (text or "").strip():
        raise ValueError("describe the terrain in a few words")
    got = vlm_spec(text, ask, current) if use_vlm else None
    if got is not None:
        spec, inherited = kept(got["spec"], text, current)
        return {"spec": spec, "line": terrain_compose.line(spec, inherited), "source": "vlm", "understood": [],
                "ignored": got["ignored"], "matched": True, "tries": got["tries"], "try": list(TRY)}
    kw = keywords(text, current)
    return {"spec": kw["spec"], "line": terrain_compose.line(kw["spec"], kw["inherited"]), "source": "keywords",
            "understood": kw["understood"], "ignored": kw["ignored"], "matched": kw["matched"], "tries": 0,
            "try": list(TRY)}


def settings(spec: TerrainCompose, seed: int, extent_m: float):
    """The TerrainSettings Apply terrain builds from a spec."""
    return terrain_compose.compose(spec, seed=seed, extent_m=extent_m)
