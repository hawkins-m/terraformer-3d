"""Describe the terrain in words (Layout tab's "Describe the terrain", `ap terrain roll --describe`):
the text becomes a terrain preset (terrain_gen.PRESETS) plus tweaks to its settings.

The local vision model's text mode does it when its server is already running (vlm.state(); it is
never started for this, and no paid provider is ever asked). Otherwise, or when its answer doesn't
validate, keyword matching does. describe() says which ("vlm" or "keywords"), what was chosen and why.
"""
import json
import re

from . import terrain_gen
from .schema import TerrainSettings

# words for each preset, most specific first (a tie goes to the earlier one)
PRESET_WORDS = {
    "island": ("island", "isle", "atoll", "archipelago"),
    "canyon": ("canyon", "gorge", "ravine", "chasm"),
    "mountain-bowl": ("mountain bowl", "ringed by mountains", "surrounded by mountains", "alpine", "bowl", "mountains"),
    "river-valley": ("river valley", "valley", "river", "stream", "delta"),
    "hill-city": ("hills", "hilly", "seven hills", "knolls", "hill"),
    "plateau": ("plateau", "mesa", "tableland", "table mountain", "highland", "terraced"),
    "coastal-plain": ("coastal", "coast", "beach", "seaside", "shore", "bay", "harbour", "harbor", "lowland"),
    "flat-plain": ("flat", "plain", "prairie", "steppe", "desert", "landlocked"),
}
# (pattern, tweak, one-line reason): applied in order, later ones win
TWEAKS = [
    (r"\bdry\b", {"river": False}, "no river"),
    (r"\briver\b|\bstream\b", {"river": True}, "a river"),
    (r"\bwide river\b|\bbroad river\b|\bgreat river\b", {"river": True, "river_w_m": 130.0}, "a wide river"),
    (r"\bnarrow river\b|\bstream\b|\bcreek\b", {"river": True, "river_w_m": 35.0}, "a narrow river"),
    (r"\bmountains?\b|\bpeaks?\b|\branges?\b", {"mountains_m": 320.0}, "mountains round it"),
    (r"\bhigh mountains\b|\btowering\b|\bsnow", {"mountains_m": 520.0}, "high mountains"),
    (r"\bflat\b|\blevel\b", {"base": "flat"}, "flat ground"),
    (r"\brolling\b|\bgentle\b|\bsoft\b", {"base": "rolling"}, "rolling ground"),
    (r"\brugged\b|\brocky\b|\bcragg?y\b|\bbroken\b|\bwild\b", {"base": "rugged"}, "rugged ground"),
    (r"\bsteep\b|\bdramatic\b", {"relief_m": 40.0}, "steep relief"),
    (r"\bhills\b|\bhilly\b|\bknolls\b", {"hills": 5}, "hills in the city"),
    (r"\bterrace[sd]?\b", {"terraces": 4, "plateau_m": 60.0}, "terraces"),
    (r"\bplateau\b|\bmesa\b", {"plateau_m": 70.0}, "a plateau"),
    (r"\bbeach(es)?\b|\bsandy\b", {"beach_m": 150.0}, "a wide beach"),
    (r"\bcliffs?\b", {"beach_m": 0.0}, "cliffs, no beach"),
    (r"\blandlocked\b|\binland\b|\bfar from the sea\b", {"coast": "none"}, "inland, no sea"),
    (r"\bisland\b|\bisle\b", {"coast": "all"}, "the sea all round"),
    (r"\bdeep canyon\b|\bdeep gorge\b", {"canyon_m": 90.0}, "a deep canyon"),
]
# a negator ("no river", "without mountains or hills") turns off the next few words, up to a comma or a
# clause word; those words count for no preset and no tweak above, and NEGATED then applies last, so it wins
_NEG = re.compile(r"\b(?:no|not|without|never|nor|free of|lacking)\b"
                  r"((?:\s+(?!(?:and|but|with|yet|though|while|where|from|near|by|in|on|at|beside)\b)[a-z'-]+){1,4})")
NEGATED = [
    (r"\b(rivers?|streams?|creeks?|delta)\b", {"river": False}, "no river"),
    (r"\b(mountains?|peaks?|ranges?|alpine)\b", {"mountains_m": 0.0}, "no mountains"),
    (r"\b(hills?|hilly|knolls?)\b", {"hills": 0}, "no hills"),
    (r"\b(seas?|ocean|coast(line)?|coastal|shore|seaside|harbou?r|bay|waterfront)\b", {"coast": "none"}, "inland, no sea"),
    (r"\bbeach(es)?\b", {"beach_m": 0.0}, "no beach"),
    (r"\b(canyons?|gorges?|ravines?)\b", {"canyon_m": 0.0}, "no canyon"),
    (r"\b(plateaus?|mesas?)\b", {"plateau_m": 0.0}, "no plateau"),
    (r"\bterrace[sd]?\b", {"terraces": 0}, "no terraces"),
]   # lakes, cliffs, islands and the like only stop counting: the generator has no setting to turn off

TWEAKABLE = ("base", "relief_m", "rise", "mountains_m", "mountains_at_m", "mountain_gaps", "hills", "hill_m", "river",
             "river_w_m", "river_depth_m", "river_angle", "canyon_m", "coast", "beach_m", "island_r_m", "plateau_m",
             "plateau_r_m", "terraces")


def _apply(name: str, tweaks: dict) -> TerrainSettings:
    base = terrain_gen.get(name)
    ok = {k: v for k, v in tweaks.items() if k in TWEAKABLE}
    return TerrainSettings.model_validate(base.model_dump() | ok | {"name": name})


def keywords(text: str, prefer: str | None = None) -> dict:
    """The preset whose words the text uses most (ties: the more specific), then the tweaks its words ask for."""
    t = " " + re.sub(r"\s+", " ", text.lower()) + " "
    negated = " ".join(m.group(1) for m in _NEG.finditer(t))
    t = _NEG.sub(" ", t)
    scores = {n: sum(t.count(f" {w}") for w in ws) for n, ws in PRESET_WORDS.items()}
    best = max(scores.values())
    name = (next(n for n in PRESET_WORDS if scores[n] == best) if best else
            (prefer if prefer in terrain_gen.PRESETS else "coastal-plain"))
    preset, tweaks, why = terrain_gen.get(name), {}, []
    for pat, tw, reason in TWEAKS:
        if re.search(pat, t):
            changed = {k: v for k, v in tw.items() if getattr(preset, k) != v}
            if changed:
                tweaks.update(changed)
                why.append(reason)
    for pat, tw, reason in NEGATED:
        if re.search(pat, negated):
            for k, v in tw.items():
                if getattr(preset, k) != v:
                    tweaks[k] = v
                else:
                    tweaks.pop(k, None)           # the preset already has it off: undo a tweak that turned it on
            why.append(reason)
    return {"preset": name, "tweaks": tweaks, "why": why, "matched": bool(best or why)}


# words shown when nothing matched (a few of what keywords() understands)
TRY_WORDS = ("island", "canyon", "mountains", "river valley", "hills", "plateau", "coast", "beach", "flat plain",
             "rugged", "rolling", "steep", "cliffs", "terraces", "inland")
_SPLIT = re.compile(r"[,;.\n]|\b(?:and|with|but|plus|then)\b")
_LEAD = {"a", "an", "the", "some", "of", "in", "on", "at", "by", "to", "around", "and", "with", "also", "really", "very"}
_FILLER = _LEAD | {"it", "its", "is", "are", "be", "there", "city", "town", "land", "terrain", "ground", "landscape",
                   "place", "area", "please", "make", "want", "like", "near", "that", "this", "has", "have", "for"}


def _vocab():
    pats = [re.escape(w) for ws in PRESET_WORDS.values() for w in ws]
    return [re.compile(r"\b(?:" + "|".join(sorted(pats, key=len, reverse=True)) + r")\w*")] + \
        [re.compile(p) for p, _, _ in TWEAKS] + [re.compile(p) for p, _, _ in NEGATED]


def understood(text: str) -> tuple[list[str], list[str]]:
    """(the words keywords() recognised, as written, a negated one as "no river"; the parts it didn't:
    clauses with no recognised word and something besides filler)."""
    t = " " + re.sub(r"\s+", " ", text.lower()).strip() + " "
    neg = [(m.start(1), m.end(1), m.group(0).split()[0]) for m in _NEG.finditer(t)]
    hits = []
    for rx in _vocab():
        for m in rx.finditer(t):
            hits.append((m.start(), m.end()))
    hits = [h for h in hits if not any(o != h and o[0] <= h[0] and h[1] <= o[1] for o in hits)]   # keep the longest
    hits = sorted(set(hits))
    seen, got = set(), []
    for a, b in hits:
        n = next((w for x, y, w in neg if x <= a and b <= y), None)
        label = f"{n} {t[a:b].strip()}" if n else t[a:b].strip()
        if label not in seen:
            seen.add(label)
            got.append(label)
    lost, pos = [], 0
    for m in list(_SPLIT.finditer(t)) + [re.search(r"$", t)]:
        a, b = pos, m.start()
        pos = m.end()
        if any(a <= h[0] and h[1] <= b + 1 for h in hits):
            continue
        words = t[a:b].split()
        while words and words[0].strip("'\"()") in _LEAD:
            words.pop(0)
        if any(w.strip("'\"()") not in _FILLER for w in words):
            lost.append(" ".join(words))
    return got, lost


SYSTEM = ("You map a short description of a landscape to one terrain preset and a few setting tweaks for a "
          "procedural terrain generator. Answer with JSON only. Choose the preset closest to the description, and "
          "tweak only what the description asks for that the preset doesn't already have. In not_understood, list "
          "any part of the description no preset or setting can show (an empty list when all of it fits).")


def _prompt(text: str) -> str:
    lines = [f"- {p['name']}: {p['description']}" for p in terrain_gen.summary()]
    fields = ", ".join(TWEAKABLE)
    return (f"Presets:\n" + "\n".join(lines) + f"\n\nSettings you may tweak: {fields}. base is flat, rolling or rugged; "
            "coast is side (sea on one side), all (an island) or none (inland); heights and widths in metres.\n\n"
            f"Description: {text.strip()}")


SCHEMA = {"type": "object", "properties": {
    "preset": {"type": "string", "enum": list(terrain_gen.PRESETS)},
    "tweaks": {"type": "object"},
    "reason": {"type": "string"},
    "not_understood": {"type": "array", "items": {"type": "string"}}}, "required": ["preset", "tweaks"]}


def vlm_pick(text: str) -> dict | None:
    """The running local model's pick, or None (no server running, an error, or an answer that
    doesn't validate)."""
    from . import vlm
    st = vlm.state()
    if not st:
        return None
    try:
        from .jobs import using
        with using("gpu0", "vlm"):
            raw = vlm.client(st).json_text(SYSTEM, _prompt(text), [], SCHEMA)
        got = json.loads(raw)
        name = got.get("preset")
        if name not in terrain_gen.PRESETS:
            return None
        tweaks = {k: v for k, v in (got.get("tweaks") or {}).items() if k in TWEAKABLE}
        _apply(name, tweaks)                      # validates (a bad value: the keywords instead)
        lost = [str(x).strip() for x in (got.get("not_understood") or []) if str(x).strip()][:6]
        return {"preset": name, "tweaks": tweaks, "why": [str(got.get("reason") or "").strip()][:1],
                "matched": True, "not_understood": lost}
    except Exception:                             # noqa: BLE001 - any failure: the keywords decide
        return None


def describe(text: str, prefer: str | None = None, use_vlm: bool = True) -> dict:
    """{preset, tweaks, settings (TerrainSettings), source: vlm | keywords, summary, matched (False: keyword
    matching found no word it knows, so the preset is only the fallback), understood, not_understood}."""
    if not (text or "").strip():
        raise ValueError("describe the terrain in a few words")
    pick = vlm_pick(text) if use_vlm else None
    source = "vlm" if pick else "keywords"
    pick = pick or keywords(text, prefer)
    s = _apply(pick["preset"], pick["tweaks"])
    tw = ", ".join(f"{k}={v}" for k, v in pick["tweaks"].items())
    why = "; ".join(w for w in pick["why"] if w)
    if source == "vlm":
        got, lost = [pick["preset"]] + [f"{k} {v}" for k, v in pick["tweaks"].items()], pick["not_understood"]
    else:
        got, lost = understood(text)
    return {"preset": pick["preset"], "tweaks": pick["tweaks"], "settings": s, "source": source,
            "matched": pick["matched"], "understood": got, "not_understood": lost, "try": list(TRY_WORDS),
            "summary": f"{pick['preset']}" + (f" with {tw}" if tw else "") + (f" ({why})" if why else "")}
