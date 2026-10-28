"""Named layout presets (city mode): the city's overall shape, as a schema.LayoutPreset that
city.plan honours. "utopia" is the generator as it was before presets and the default, so a
layout without a preset (every existing project) plans exactly as before.

A project keeps its preset in layout.json (city.preset, with every field, so editing one field
by hand works); `apply` sets one by name and leaves the rest of the city as it is.
"""
from .schema import LayoutPreset, SiteLayout

PRESETS: dict[str, LayoutPreset] = {p.name: p for p in [
    LayoutPreset(),                                                        # utopia: radial, coastal, as before
    LayoutPreset(name="grid", streets="grid", outline="coastal", block_parks=0.04, tall_at="centre",
                 height_falloff_m=900),
    LayoutPreset(name="ring-city", streets="ring", outline="circle", rings=3, falloff="linear",
                 highways="ring"),
    LayoutPreset(name="walled-town", streets="organic", outline="circle", walls="wall_moat", falloff="flat",
                 falloff_scale=0.8),
    LayoutPreset(name="linear", streets="linear", outline="strip", tall_at="waterfront", height_falloff_m=500),
    LayoutPreset(name="organic", streets="organic", outline="blob", block_parks=0.06, tall_at="nodes"),
    LayoutPreset(name="metro", streets="grid", outline="blob", rings=1, tall_at="centre", height_falloff_m=1200,
                 falloff_scale=1.3, highways="both"),
]}
DEFAULT = "utopia"


def get(name: str) -> LayoutPreset:
    if name not in PRESETS:
        raise ValueError(f"unknown layout preset {name!r} (presets: {', '.join(PRESETS)})")
    return PRESETS[name].model_copy(deep=True)


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
    """For the UI: each preset's name and its fields that differ from utopia."""
    base = LayoutPreset().model_dump()
    return [{"name": n, "changes": {k: v for k, v in p.model_dump().items() if k != "name" and v != base[k]}}
            for n, p in PRESETS.items()]
