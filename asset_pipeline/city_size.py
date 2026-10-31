"""City size (CitySpec.size, the Layout tab's City size, `ap site roll --size`, `ap site size`):
standard (the city as it always was), large (about 8 km across) or metro (about 14 km).

A large or metro city is its standard one scaled up, then shaped and planned again:
    bounds      scaled round the origin to ACROSS_M along their long side (k = that / the standard's)
    terrain     every horizontal distance of the coast, hills, mountains, river and plateau x k (the
                same landscape, bigger), the tile grown to cover the city plus MARGIN_M, finer samples
                (RESOLUTION) when it grows past what 2017 samples hold at ~6 m, its z range refitted
    shape       a preset with a shape recipe (city_shape.py) is shaped again on the new ground with the
                city seed: classic's downtown, rings, street grid, river and highways follow the bounds
                (city_metro scales with them, plus factor() where counts don't); other presets' footprint
    plan        city.plan reads factor() for what doesn't follow the bounds (classic's large parks,
                highway spokes, tower cap; other presets' outline_m, ring highway, downtown)

The standard bounds and terrain are kept in CitySpec.sized_from, so going back to standard restores
them exactly (then shapes again with the same seed). A standard city never reads anything here:
factor() is 1 and every gate in city.py / city_metro.py is `if k != 1`, so every project and preset
plans byte for byte as before (tests/test_city_size.py pins it).
"""
import math

from .schema import CitySize, CivicNode, SiteLayout, TerrainSpec

# name: (label, about this across, m; None: the city as it is)
SIZES: dict[str, tuple[str, float | None]] = {
    "standard": ("Standard", None),
    "large": ("Large", 8000.0),
    "metro": ("Metro", 14000.0),
}
MARGIN_M = 1500.0               # the generated terrain covers the city's bounds plus this all round
TILE_STEP_M = 500.0             # the tile's extent is rounded up to this
CELL_MAX_M = 6.5                # coarser samples than this: the next resolution up
RESOLUTION = (2017, 4033)       # UE-legal Landscape sizes
# a TerrainSpec's / TerrainSettings' horizontal distances that scale with the city (x k), and the ones
# that scale more gently (x sqrt k: a hill's size, a river's meander)
SPEC_K = ("shore_m", "headland_width_m", "hill_from_m", "hill_to_m", "coast_wavelength_m", "bay_m",
          "bay_lateral_m", "bay_width_m", "city_radius_m")
SETTINGS_K = ("city_r_m", "mountains_at_m", "mountains_w_m", "plateau_r_m", "island_r_m", "river_offset_m")
SETTINGS_SQRT_K = ("hill_r_m", "river_meander_m")
EXPERIMENTAL = ("large", "metro")   # labelled experimental in the UI (Unreal builds measured over 20 min)


def names() -> list[str]:
    return list(SIZES)


def check(size: str) -> CitySize:
    if size not in SIZES:
        raise ValueError(f"unknown city size {size!r} (sizes: {', '.join(SIZES)})")
    return size  # type: ignore[return-value]


def across(bounds) -> float:
    x0, y0, x1, y1 = bounds
    return max(x1 - x0, y1 - y0)


def factor(c) -> float:
    """How much bigger than its standard city this city is (1.0 for standard)."""
    if c is None or c.size == "standard" or not c.sized_from:
        return 1.0
    return across(c.bounds) / across(c.sized_from["bounds"])


DETAIL_RADIUS_M = 2500.0         # the [city] detail_radius_m setting's fallback


def default_detail_radius() -> float:
    """The [city] detail_radius_m setting."""
    from . import config
    return float(config.get("city.detail_radius_m", DETAIL_RADIUS_M) or DETAIL_RADIUS_M)


def detail_radius(c) -> float | None:
    """How far from a shot camera or downtown library builds stand in a large / metro city (None:
    a standard city, everywhere): the project's own, else the setting."""
    if c is None or c.size == "standard":
        return None
    return float(c.detail_radius_m or default_detail_radius())


def summary() -> list[dict]:
    """For the UI: each size's name, label and how far across it is (None: as the city is)."""
    return [{"name": n, "label": lab, "across_m": a, "experimental": n in EXPERIMENTAL} for n, (lab, a) in SIZES.items()]


def _base(layout: SiteLayout) -> SiteLayout:
    """The standard city this one is (or was scaled from)."""
    c = layout.city
    if c.size == "standard" or not c.sized_from:
        return layout
    L = layout.model_copy(deep=True)
    L.city.bounds = tuple(c.sized_from["bounds"])
    L.terrain = TerrainSpec.model_validate(c.sized_from["terrain"])
    if "nodes" in c.sized_from:
        L.city.nodes = [CivicNode.model_validate(n) for n in c.sized_from["nodes"]]
    L.city.size, L.city.sized_from = "standard", None
    return SiteLayout.model_validate(L.model_dump(mode="json"))


def scaled_terrain(t: TerrainSpec, k: float, bounds) -> TerrainSpec:
    """The terrain x k (see the module docstring), its tile covering `bounds` plus MARGIN_M."""
    from . import greybox, terrain_gen
    up = {f: round(getattr(t, f) * k, 1) for f in SPEC_K}
    half = max(abs(v) for v in bounds) + MARGIN_M
    extent = max(t.extent_m, math.ceil(2 * half / TILE_STEP_M) * TILE_STEP_M)
    res = next((r for r in RESOLUTION if extent / (r - 1) <= CELL_MAX_M), RESOLUTION[-1])
    up |= {"extent_m": extent, "resolution": max(t.resolution, res)}
    if t.settings is not None:
        s = t.settings
        su = {f: round(getattr(s, f) * k, 1) for f in SETTINGS_K}
        su |= {f: round(getattr(s, f) * math.sqrt(k), 1) for f in SETTINGS_SQRT_K}
        su["centre"] = (round(s.centre[0] * k, 1), round(s.centre[1] * k, 1))
        up["settings"] = s.model_copy(update=su)
    t2 = t.model_copy(update=up)
    if t2.heightmap is None:
        h = terrain_gen.make(t2) if t2.settings is not None else greybox.make_terrain(t2)
        lo, hi = t2.z_range
        if float(h.min()) < lo or float(h.max()) > hi:
            t2 = t2.model_copy(update={"z_range": terrain_gen.z_range_for(h)})
    return t2


def sized(layout: SiteLayout, size: str) -> SiteLayout:
    """The layout at this size: its standard city (restored exactly when it was scaled), scaled
    up for large and metro (bounds and terrain; not shaped or planned here). A heightmap terrain
    can't grow: its city stays standard."""
    check(size)
    if layout.city is None:
        raise ValueError("city sizes are for city layouts (this layout has no city spec)")
    base = _base(layout)
    if size == "standard":
        return base
    if base.terrain.heightmap or base.terrain.far:
        raise ValueError("the terrain is a heightmap (a terrain build): only a generated terrain grows with the city")
    target = SIZES[size][1]
    k = max(1.0, target / across(base.city.bounds))
    L = base.model_copy(deep=True)
    c = L.city
    c.sized_from = {"bounds": list(base.city.bounds), "terrain": base.terrain.model_dump(mode="json"),
                    "nodes": [n.model_dump(mode="json") for n in base.city.nodes]}
    c.size = size
    c.bounds = tuple(round(v * k) for v in base.city.bounds)
    c.nodes = [n.model_copy(update={"center": (round(n.center[0] * k, 1), round(n.center[1] * k, 1))}) for n in c.nodes]
    if c.enclosure is not None:
        c.enclosure = c.enclosure.model_copy(update={"center": (c.enclosure.center[0] * k, c.enclosure.center[1] * k),
                                                     "radius": round(c.enclosure.radius * k)})
    L.terrain = scaled_terrain(base.terrain, k, c.bounds)
    return SiteLayout.model_validate(L.model_dump(mode="json"))


def shaped(store, layout: SiteLayout) -> SiteLayout:
    """The layout's districts placed again on its (new) ground: its preset's shape recipe with the
    city seed, else the districts moved onto land (as a roll does)."""
    from . import city_shape
    from .stages import sw_site
    ground = sw_site.plan_ground(store, layout)
    if city_shape.has_recipe(layout.city.preset.name):
        L = city_shape.shape(layout, ground, layout.city.preset.name, layout.city.seed)
        L.city.preset, L.city.layout_seed = layout.city.preset.model_copy(deep=True), layout.city.layout_seed
        return L
    return city_shape.fit_nodes(layout, ground)


def set_size(store, size: str, background: bool | None = None, detail_radius_m: float | None = None,
             log=print, shots: bool = True) -> dict:
    """Resize the project's city (and/or turn its background region on or off, set its detail
    radius), shape it again, plan it and place the auto shots. Returns the plan's stats."""
    from . import city_roll
    from .stages import sw_site
    layout = city_roll._layout_or_starter(store)
    if check(size) == layout.city.size:      # the same size: the city stays as it is (flags only)
        L = layout.model_copy(deep=True)
    else:
        L = shaped(store, sized(layout, size))
    if background is not None:
        L.city.background = bool(background)
    if detail_radius_m is not None:
        L.city.detail_radius_m = detail_radius_m if detail_radius_m > 0 else None
    L = SiteLayout.model_validate(L.model_dump(mode="json"))
    log(f"city size {size}: {across(L.city.bounds) / 1000:.1f} km across, terrain {L.terrain.extent_m / 1000:.1f} km"
        + (" with its background region" if L.city.background else ""))
    stats = city_roll._plan(store, L, log)
    if shots:
        sw_site.set_city_shots(store)
    store.log_run({"stage": "site.size", "size": size, "background": L.city.background, "buildings": stats.get("buildings")})
    return stats
