"""Named layout presets (city mode): the city's overall shape, as a schema.LayoutPreset that
city.plan honours. "utopia" is the generator as it was before presets and the default, so a
layout without a preset (every existing project) plans exactly as before.

New projects start on "classic" (NEW_PROJECT); a roll can pick one at random ("random").
A project keeps its preset in layout.json (city.preset, with every field, so editing one field
by hand works); `apply` sets one by name and leaves the rest of the city as it is.
"""
from .schema import LayoutPreset, SiteLayout

P = LayoutPreset
# name: (one line for the Layout picker, the preset)
CATALOG: dict[str, tuple[str, LayoutPreset]] = {
    "utopia": ("Radial avenues out of each civic node on the coast; the generator as it was before presets.", P()),
    "classic": ("An ordinary modern city: a grid, a tight downtown of towers, a mid-rise ring, wide suburbs, "
                "ring and radial highways with interchanges, industry by a highway.",
                P(streets="grid", outline="blob", rings=1, tall_at="centre", height_falloff_m=700, falloff_scale=1.3,
                  downtown_m=420, tower_storeys=(50, 18), suburbs_m=800, highways="both", highway_spokes=4,
                  industrial="highway", clear_overlaps=True)),
    "grid": ("A plain grid of avenues with grid-aligned blocks, a few parks, tallest in the middle.",
             P(streets="grid", block_parks=0.04, tall_at="centre", height_falloff_m=900, clear_overlaps=True)),
    "manhattan": ("A long narrow grid strip, water on its long sides: long avenues, short cross streets, two tower clusters.",
                  P(streets="grid", outline="strip", strip_water=True, grid_m=260, grid_ratio=0.35, tall_at="nodes",
                    height_falloff_m=500, downtown_m=300, tower_storeys=(55, 22), clear_overlaps=True)),
    "park-grid": ("Grid blocks separated by parks: every other grid cell is green.",
                  P(streets="grid", block_parks=0.5, park_pattern="checker", max_storeys=6, clear_overlaps=True)),
    "suburbia": ("Low suburbs with winding streets and cul-de-sacs, a small centre, radial highways.",
                 P(streets="organic", outline="blob", falloff_scale=1.6, suburbs_m=350, cul_de_sacs=True,
                   max_storeys=4, highways="radial", highway_spokes=3, clear_overlaps=True)),
    "garden-city": ("A garden city: wards separated by green rings, a radial plan, low and leafy.",
                    P(streets="radial", outline="circle", rings=2, park_rings=True, block_parks=0.1, max_storeys=4,
                      falloff="flat", clear_overlaps=True)),
    "concentric": ("A fantasy concentric city: rings within rings round the centre, tallest at the heart.",
                   P(streets="ring", outline="circle", rings=5, falloff="linear", tall_at="centre",
                     height_falloff_m=600, highways="ring", clear_overlaps=True)),
    "walled-castle": ("A castle town: an inner ward wall round the keep, an outer wall and moat, ring lanes between.",
                      P(streets="organic", outline="circle", rings=2, walls="wall_moat", inner_wall=True,
                        falloff="flat", falloff_scale=0.8, max_storeys=5, clear_overlaps=True)),
    "star-fortress": ("A star fortress: a star-shaped bastioned wall and moat round a parade, a regular grid inside.",
                      P(streets="grid", outline="circle", walls="wall_moat", wall_shape="star", falloff="flat",
                        max_storeys=5, clear_overlaps=True)),
    "linear": ("A linear city: one spine along the coast with cross avenues, tallest at the waterfront.",
               P(streets="linear", outline="strip", tall_at="waterfront", height_falloff_m=500, clear_overlaps=True)),
    "organic": ("An organic town: winding, jittered avenues in a wobbly outline, parks between blocks.",
                P(streets="organic", outline="blob", block_parks=0.06, tall_at="nodes", clear_overlaps=True)),
    "canal-city": ("A canal city: canals along the coast through a grid of blocks, tallest by the water.",
                   P(streets="grid", outline="blob", canals=4, grid_deg=0, tall_at="waterfront", height_falloff_m=600,
                     max_storeys=6, clear_overlaps=True)),
    "river-town": ("A river town: a river winds from the hills to the sea past the centre, radial streets.",
                   P(streets="radial", outline="blob", river=True, falloff_scale=0.9, max_storeys=5,
                     clear_overlaps=True)),
    "harbour-port": ("A harbour port: dense waterfront, many piers, docks and warehouses beside the harbour.",
                     P(streets="linear", outline="coastal", tall_at="waterfront", height_falloff_m=400, piers=7,
                       industrial="harbour", industrial_m=300, clear_overlaps=True)),
    "island-city": ("An island city: a wobbly island with the sea all round, a shore promenade, piers out.",
                    P(streets="radial", outline="island", tall_at="centre", height_falloff_m=700, piers=5,
                      clear_overlaps=True)),
    "hill-town": ("A hill town: every quarter on a summit with winding lanes and stairs, terraced slopes.",
                  P(streets="organic", outline="blob", organic_nodes=True, terraces=True, max_slope=0.4,
                    max_storeys=4, clear_overlaps=True)),
    "cliff-town": ("A terraced cliff town: contour terraces down a steep slope to the sea, lanes and stairs.",
                   P(streets="organic", outline="strip", terraces=True, max_slope=0.6, organic_nodes=True,
                     tall_at="waterfront", height_falloff_m=300, max_storeys=4, clear_overlaps=True)),
    "market-town": ("A crossroads market town: two roads crossing at the market, a small compact town.",
                    P(streets="crossroads", outline="circle", outline_m=650, falloff="flat", falloff_scale=0.7,
                      max_storeys=4, clear_overlaps=True)),
    "campus": ("A campus or temple complex: a green mall on the main axis, low halls on a grid, lawns between.",
               P(streets="grid", outline="circle", mall_w=90, block_parks=0.25, max_storeys=4, falloff="flat",
                 clear_overlaps=True)),
}
PRESETS: dict[str, LayoutPreset] = {n: p.model_copy(update={"name": n}) for n, (_, p) in CATALOG.items()}
DESCRIPTIONS: dict[str, str] = {n: d for n, (d, _) in CATALOG.items()}
ALIASES = {"metro": "classic", "ring-city": "concentric", "walled-town": "walled-castle"}   # renamed on 2026-10-27
DEFAULT = "utopia"          # a layout without a preset (every project made before presets)
NEW_PROJECT = "classic"     # a new project's starter city
RANDOM = "random"           # a roll picks one from its seed
# the preset fields that make the city's shape: a roll with "Keep current outline and districts"
# keeps the layout's own (new streets only)
SHAPE_FIELDS = ("outline", "outline_m", "walls", "wall_shape", "moat_w", "bastions", "inner_wall", "strip_water",
                "canals", "canal_w", "river", "river_w", "organic_nodes", "max_slope")


def get(name: str) -> LayoutPreset:
    name = ALIASES.get(name, name)
    if name not in PRESETS:
        raise ValueError(f"unknown layout preset {name!r} (presets: {', '.join(PRESETS)})")
    return PRESETS[name].model_copy(deep=True)


def random_name(seed: int) -> str:
    """The preset a roll with "random" gets: drawn from the seed on its own stream."""
    import numpy as np
    names = list(PRESETS)
    return names[int(np.random.default_rng([0x9E5E7, int(seed)]).integers(len(names)))]


def apply(layout: SiteLayout, name: str, layout_seed: int | None = None) -> SiteLayout:
    """A copy of the city layout with this preset (and layout seed, if given); nothing else changes."""
    if layout.city is None:
        raise ValueError("layout presets are for city layouts (this layout has no city spec)")
    L = layout.model_copy(deep=True)
    L.city.preset = get(name)
    if layout_seed is not None:
        L.city.layout_seed = int(layout_seed)
    return SiteLayout.model_validate(L.model_dump(mode="json"))


def summary() -> list[dict]:
    """For the UI: each preset's name, its one-line description and its fields that differ from utopia."""
    base = LayoutPreset().model_dump()
    return [{"name": n, "description": DESCRIPTIONS[n],
             "changes": {k: v for k, v in p.model_dump().items() if k != "name" and v != base[k]}}
            for n, p in PRESETS.items()]
