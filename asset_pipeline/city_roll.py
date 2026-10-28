"""Roll a city (Layout tab's dice, `ap site roll`): random city rules within conservative ranges
plus a seed, so a new project starts from a believable city instead of a blank one.

The rules come in groups the user can keep (lock) while the rest are rolled:
    size        the city's bounds
    civic       civic nodes: how many, where, their size, role, pull and monument
    density     the density gradient: falloff, the urban edge, storeys core .. outskirts
    streets     the street pattern: radials, their turn and spiral per node, markets, block and
                parcel sizes
    green       green corridors and their width, the park share
    waterfront  the coast: shoreline distance, its irregularity, a bay, the headland

A roll is reproducible: the same seed and the same locked values give the same city (every group
is drawn from the seed in a fixed order, whether it is kept or not). The seed is also the city
plan's own seed. After a roll the plan is made with the existing re-plan code
(sw_site.plan_city); a random roll whose plan fails the housing-share check (city.stats) is rolled
again with the next seed, a typed seed is kept as it is. The auto shots are placed again for the
new city (locked shots and the user's shot edits stay, as sw_site.set_city_shots does).

The layout preset (the city's overall shape, layout_presets.py) and its layout seed are not rule
groups: a roll keeps the layout's own unless it is given one, and records both, so Previous / Next
restore them too; preset "random" draws one from the roll's seed. A preset with a shape recipe
(city_shape.py: every one but utopia) owns the whole city shape on a roll: outline, wall, districts,
landmarks and satellite towns, placed on the plan's ground; `keep_shape` leaves the outline and
districts as they are (new streets only). A heightmap terrain keeps its waterfront rules (its coast
is in the heightmap, the rules can't move it). `set_preset` changes the preset alone and re-plans (the rules and the seed stay).

History: site/rolls.json keeps the rules and seed of the last ROLLS_KEPT rolls ({rolls, pos});
Previous / Next roll restore one and re-plan (no plan files are stored).
"""
import math
from datetime import datetime, timezone

import numpy as np

from . import city_shape, layout_presets
from .project import ProjectStore, read_json, write_json
from .schema import CivicNode, LayoutPreset, SiteLayout

GROUPS = {"size": "City size", "civic": "Civic nodes", "density": "Density", "streets": "Street pattern",
          "green": "Green space", "waterfront": "Waterfront"}
ROLLS_KEPT = 10
TRIES = 6                       # a random roll whose plan fails the housing check: this many seeds at most
SEED_MAX = 2**31 - 1


def rolls_path(store: ProjectStore):
    from .stages import sw_site
    return sw_site.site_dir(store) / "rolls.json"


# --- the rules of a layout, per group ---------------------------------------------------------

def rules_of(layout: SiteLayout) -> dict:
    """The layout's current values of every group (JSON-able)."""
    c, t = layout.city, layout.terrain
    return {
        "size": {"bounds": list(c.bounds)},
        "civic": {"nodes": [{k: (list(v) if isinstance(v, tuple) else v) for k, v in
                             n.model_dump(include={"id", "center", "radius", "role", "monument", "weight",
                                                   "avenue_m", "layout", "centre", "civic_ring",
                                                   "stoa_share"}).items()} for n in c.nodes]},
        "density": {"density_falloff_m": c.density_falloff_m, "urban_threshold": c.urban_threshold,
                    "storeys": list(c.storeys)},
        "streets": {"nodes": {n.id: {"radials": n.radials, "rot": n.rot, "spiral_deg_per_100m": n.spiral_deg_per_100m}
                              for n in c.nodes},
                    "markets": c.markets, "block_m": list(c.block_m), "parcel_m": list(c.parcel_m)},
        "green": {"green_corridors": c.green_corridors, "corridor_w": c.corridor_w, "park_share": c.park_share},
        "waterfront": {"shore_m": t.shore_m, "coast_amp_m": t.coast_amp_m, "coast_wavelength_m": t.coast_wavelength_m,
                       "bay_m": t.bay_m, "bay_lateral_m": t.bay_lateral_m, "headland_m": t.headland_m},
    }


def _r(rng, a, b, nd=0):
    v = float(rng.uniform(a, b))
    return round(v, nd) if nd else round(v)


def random_rules(seed: int) -> dict:
    """Every group drawn from the seed, in a fixed order (locks don't shift the others). Ranges are
    kept close to the starter city's, so every roll is a believable coastal city."""
    rng = np.random.default_rng(seed)
    hw, hh = _r(rng, 2000, 2800), _r(rng, 1200, 1700)
    size = {"bounds": [-hw, -hh, hw, hh]}
    shore = min(max(hh + _r(rng, -50, 200), 1150), 1750)    # the city's edge reaches the water
    waterfront = {"shore_m": shore, "coast_amp_m": _r(rng, 60, 180), "coast_wavelength_m": _r(rng, 1800, 3200),
                  "bay_m": _r(rng, 0, 220) if rng.random() < 0.5 else 0, "bay_lateral_m": _r(rng, -900, 900),
                  "headland_m": _r(rng, 80, 220)}
    # civic nodes: a forum near the middle and a harbour by the water, then 1-3 of market, hill,
    # quarter; centres kept apart and on land (the sea is towards -y, starting `shore` from 0)
    specs = [("centre", "forum", (260, 320), (1.15, 1.4)), ("harbour", "harbour", (150, 190), (0.9, 1.1))]
    extra = [("market", "market", (130, 170), (0.7, 0.9)), ("hill", "hill", (170, 220), (0.8, 1.0)),
             ("quarter", "local", (130, 170), (0.7, 0.9))]
    rng.shuffle(extra)
    specs += extra[:int(rng.integers(1, 4))]
    nodes, placed = [], []
    for nid, role, (r0, r1), (w0, w1) in specs:
        radius, weight = _r(rng, r0, r1), _r(rng, w0, w1, 2)
        for _ in range(60):
            if role == "forum":
                x, y = _r(rng, -0.15 * hw, 0.15 * hw), _r(rng, -0.5 * hh, -0.3 * hh)
            elif role == "harbour":
                x, y = _r(rng, -0.7 * hw, 0.7 * hw), -shore + _r(rng, 300, 360)
            elif role == "hill":
                x, y = _r(rng, -0.6 * hw, 0.6 * hw), _r(rng, 0.25 * hh, 0.6 * hh)
            else:
                x, y = _r(rng, -0.7 * hw, 0.7 * hw), _r(rng, -0.55 * hh, 0.45 * hh)
            if all(math.hypot(x - px, y - py) > radius + pr + 450 for px, py, pr in placed):
                break
        placed.append((x, y, radius))
        nodes.append({"id": nid, "center": [x, y], "radius": radius, "role": role,
                      "monument": (["rotunda", "temple"][int(rng.integers(2))] if role == "forum" else None),
                      "weight": weight, "avenue_m": _r(rng, 1500, 2000) if role == "forum" else 1200,
                      "layout": "radial"})
    civic = {"nodes": nodes}
    density = {"density_falloff_m": _r(rng, 560, 740), "urban_threshold": _r(rng, 0.10, 0.15, 3),
               "storeys": [int(rng.integers(4, 6)), int(rng.integers(7, 10)), 2, int(rng.integers(3, 5))]}
    streets = {"nodes": {n["id"]: {"radials": int(rng.integers(7, 10)) if n["role"] == "forum" else int(rng.integers(5, 8)),
                                   "rot": _r(rng, 0, 45),
                                   "spiral_deg_per_100m": _r(rng, 0, 6, 1) if n["role"] == "forum" else _r(rng, 0, 2.5, 1)}
                         for n in nodes},
               "markets": int(rng.integers(2, 6)), "block_m": [_r(rng, 80, 100), _r(rng, 150, 190)],
               "parcel_m": [_r(rng, 20, 24), _r(rng, 30, 36)]}
    green = {"green_corridors": int(rng.integers(1, 5)), "corridor_w": _r(rng, 60, 90),
             "park_share": _r(rng, 0.04, 0.10, 3)}
    return {"size": size, "civic": civic, "density": density, "streets": streets, "green": green,
            "waterfront": waterfront}


def apply_rules(layout: SiteLayout, rules: dict, seed: int) -> SiteLayout:
    """A copy of the layout with these rules and the seed (validated as a SiteLayout)."""
    L = layout.model_copy(deep=True)
    c, t = L.city, L.terrain
    c.seed = int(seed)
    c.bounds = tuple(rules["size"]["bounds"])
    old = {n.id: n for n in c.nodes}
    nodes = []
    for nd in rules["civic"]["nodes"]:
        base = old[nd["id"]].model_dump() if nd["id"] in old else {}
        nodes.append(CivicNode.model_validate(base | nd))
    st = rules["streets"]
    for n in nodes:                       # streets per node by id; a node new to them keeps its own
        n.radials, n.rot, n.spiral_deg_per_100m = (st["nodes"][n.id][k] for k in ("radials", "rot", "spiral_deg_per_100m")) \
            if n.id in st["nodes"] else (n.radials, n.rot, n.spiral_deg_per_100m)
    c.nodes = nodes
    c.markets, c.block_m, c.parcel_m = st["markets"], tuple(st["block_m"]), tuple(st["parcel_m"])
    d = rules["density"]
    c.density_falloff_m, c.urban_threshold, c.storeys = d["density_falloff_m"], d["urban_threshold"], tuple(d["storeys"])
    g = rules["green"]
    c.green_corridors, c.corridor_w, c.park_share = g["green_corridors"], g["corridor_w"], g["park_share"]
    for k, v in rules["waterfront"].items():
        setattr(t, k, v)
    return SiteLayout.model_validate(L.model_dump(mode="json"))


def rolled(layout: SiteLayout, seed: int, keep=()) -> tuple[SiteLayout, dict]:
    """The layout rolled with this seed, the groups in `keep` left as they are. Returns (layout,
    the full rules it now has)."""
    bad = set(keep) - set(GROUPS)
    if bad:
        raise ValueError(f"unknown rule group(s): {', '.join(sorted(bad))} (groups: {', '.join(GROUPS)})")
    rules, now = random_rules(seed), rules_of(layout)
    for g in keep:
        rules[g] = now[g]
    return apply_rules(layout, rules, seed), rules


# --- history -------------------------------------------------------------------------------

def history(store: ProjectStore) -> dict:
    """{rolls: [{seed, rules, keep, at, warnings}], pos (the current one, or -1)}."""
    h = read_json(rolls_path(store), default=None) or {}
    rolls = h.get("rolls") or []
    return {"rolls": rolls, "pos": min(h.get("pos", len(rolls) - 1), len(rolls) - 1)}


def _remember(store: ProjectStore, entry: dict) -> dict:
    h = history(store)
    rolls = (h["rolls"] + [entry])[-ROLLS_KEPT:]
    out = {"rolls": rolls, "pos": len(rolls) - 1}
    write_json(rolls_path(store), out)
    return out


def summary(store: ProjectStore) -> dict:
    """For the UI: the groups, the current seed, the history (seeds only) and where it stands."""
    from .stages import sw_site
    h = history(store)
    seed, preset, layout_seed = None, layout_presets.DEFAULT, 0
    try:
        lay = sw_site.load_layout(store)
        if lay.city:
            seed, preset, layout_seed = lay.city.seed, lay.city.preset.name, lay.city.layout_seed
    except FileNotFoundError:
        pass
    return {"groups": GROUPS, "seed": seed, "preset": preset, "layout_seed": layout_seed,
            "presets": layout_presets.summary(), "pos": h["pos"], "count": len(h["rolls"]),
            "seeds": [r["seed"] for r in h["rolls"]], "kept": ROLLS_KEPT,
            "last": ({k: h["rolls"][h["pos"]].get(k) for k in ("seed", "keep", "warnings", "at")} if h["rolls"] else None)}


# --- rolling -------------------------------------------------------------------------------

def _housing_fails(stats: dict) -> bool:
    return any(w.startswith("housing is") for w in stats.get("warnings", []))


def _layout_or_starter(store: ProjectStore) -> SiteLayout:
    """The project's city layout; a project with none starts from the starter city. A project
    with its own (non-city) layout can't roll a city."""
    from .stages import sw_site
    try:
        lay = sw_site.load_layout(store)
    except FileNotFoundError:
        lay = sw_site.init(store, city_mode=True)
    if lay.city is None:
        raise ValueError("this project's layout is its own (no city rules): rolling a city needs a city layout")
    return lay


def _plan(store: ProjectStore, layout: SiteLayout, log) -> dict:
    from .stages import sw_site
    sw_site.save_layout(store, layout)
    log(f"planning the city (seed {layout.city.seed})")
    return sw_site.plan_city(store)


def _with_shape(layout: SiteLayout, preset: str | None, layout_seed: int | None) -> SiteLayout:
    """The layout with this named preset and/or layout seed (None: its own)."""
    if preset is not None:
        return layout_presets.apply(layout, preset, layout_seed)
    if layout_seed is not None:
        L = layout.model_copy(deep=True)
        L.city.layout_seed = int(layout_seed)
        return SiteLayout.model_validate(L.model_dump(mode="json"))
    return layout


def set_preset(store: ProjectStore, preset: str | None = None, layout_seed: int | None = None, log=print) -> dict:
    """Change the layout preset and/or layout seed alone and re-plan (rules, seed and shots stay).
    Returns the stats."""
    layout = _with_shape(_layout_or_starter(store), preset, layout_seed)
    stats = _plan(store, layout, log)
    store.log_run({"stage": "site.preset", "preset": layout.city.preset.name, "layout_seed": layout.city.layout_seed,
                   "buildings": stats.get("buildings")})
    return stats


def _keep_shape_of(layout: SiteLayout, own: SiteLayout) -> SiteLayout:
    """The new preset's streets on the layout's own outline, walls and water."""
    L = layout.model_copy(deep=True)
    L.city.preset = L.city.preset.model_copy(update={k: getattr(own.city.preset, k) for k in layout_presets.SHAPE_FIELDS})
    return L


def roll(store: ProjectStore, seed: int | None = None, keep=(), log=print, shots: bool = True,
         preset: str | None = None, layout_seed: int | None = None, keep_shape: bool = False) -> dict:
    """Roll the city rules (the groups in `keep` stay) with `seed` (None: a random one, rolled
    again while the plan fails the housing-share check), save, re-plan and remember it. `preset`
    / `layout_seed`: the city's shape (None: the layout's own; preset "random": one drawn from each
    seed tried). Returns {seed, keep, stats, tries, history}."""
    from .stages import sw_site
    own = _layout_or_starter(store)
    if preset is not None and preset != layout_presets.RANDOM:
        layout_presets.get(preset)           # an unknown name fails before anything is saved
    keep = set(keep)
    if own.terrain.heightmap:
        keep.add("waterfront")
    if keep_shape:
        keep |= {"size", "civic", "waterfront"}
    typed = seed is not None
    seeds = [int(seed)] if typed else [int(s) for s in np.random.default_rng().integers(1, SEED_MAX, TRIES)]
    for i, s in enumerate(seeds):
        if not 0 <= s <= SEED_MAX:
            raise ValueError(f"seed must be 0..{SEED_MAX}")
        base = _with_shape(own, layout_presets.random_name(s) if preset == layout_presets.RANDOM else preset, layout_seed)
        if keep_shape:
            base = _keep_shape_of(base, own)
        layout, rules = rolled(base, s, keep)
        shaped = not keep_shape and "civic" not in keep and city_shape.has_recipe(layout.city.preset.name)
        if shaped:
            try:
                layout = city_shape.shape(layout, sw_site.plan_ground(store, layout), layout.city.preset.name, s)
            except ValueError as e:
                if typed or i == len(seeds) - 1:
                    raise
                log(f"seed {s}: {e}; rolling again")
                continue
            rules = rules_of(layout)
        elif "civic" not in keep:       # rolled districts placed by the rules' coast: none left in the sea
            fitted = city_shape.fit_nodes(layout, sw_site.plan_ground(store, layout))
            if fitted is not layout:
                layout, rules = fitted, rules_of(fitted)
        stats = _plan(store, layout, log)
        if typed or not _housing_fails(stats):
            break
        log(f"seed {s}: {next(w for w in stats['warnings'] if w.startswith('housing is'))}; rolling again")
    if shots:
        sw_site.set_city_shots(store)
    h = _remember(store, {"seed": s, "rules": rules, "keep": sorted(keep), "warnings": stats.get("warnings", []),
                          "preset": layout.city.preset.model_dump(), "layout_seed": layout.city.layout_seed,
                          **({"shape": {"enclosure": layout.city.enclosure.model_dump(mode="json") if layout.city.enclosure else None,
                                        "towns": [tw.model_dump(mode="json") for tw in layout.city.towns],
                                        "sites": [st.model_dump(mode="json") for st in layout.city.sites]}}
                             if shaped else {}),
                          "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    store.log_run({"stage": "site.roll", "seed": s, "keep": sorted(keep), "preset": layout.city.preset.name, "tries": i + 1, "buildings": stats.get("buildings")})
    return {"seed": s, "keep": sorted(keep), "stats": stats, "tries": i + 1, "history": {"pos": h["pos"], "count": len(h["rolls"])}}


def step(store: ProjectStore, delta: int, log=print, shots: bool = True) -> dict:
    """Previous (-1) / next (+1) roll: its rules and seed back, re-planned. Returns {seed, pos, count, stats}."""
    from .stages import sw_site
    h = history(store)
    pos = h["pos"] + delta
    if not h["rolls"] or not 0 <= pos < len(h["rolls"]):
        raise ValueError("no previous roll" if delta < 0 else "no next roll")
    r = h["rolls"][pos]
    layout = apply_rules(_layout_or_starter(store), r["rules"], r["seed"])
    if "preset" in r:       # rolls from before presets keep the layout's own shape
        layout.city.preset = LayoutPreset.model_validate(r["preset"])
        layout.city.layout_seed = int(r.get("layout_seed", 0))
    if "shape" in r:        # a preset's own wall, towns and civic sites
        layout = SiteLayout.model_validate(layout.model_dump(mode="json") | {"city": layout.city.model_dump(mode="json") | {
            "enclosure": r["shape"]["enclosure"], "towns": r["shape"]["towns"], "sites": r["shape"]["sites"]}})
    stats = _plan(store, layout, log)
    if shots:
        sw_site.set_city_shots(store)
    write_json(rolls_path(store), {"rolls": h["rolls"], "pos": pos})
    return {"seed": r["seed"], "pos": pos, "count": len(h["rolls"]), "stats": stats}
