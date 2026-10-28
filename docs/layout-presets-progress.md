# Layout presets: progress

## How the city plan and Roll a city work (before presets)

**City plan** (`asset_pipeline/city.py`, `city.plan(layout, heights)`; run by `sw_site.plan_city`,
`ap site plan`, and the greybox build):

1. **Fields.** `Fields` samples the terrain (height, slope, land, distance inland) and a density
   field: each civic node pulls with a Gaussian `exp(-(d / (density_falloff_m * weight))^2)`, the
   waterfront adds a little, low-frequency noise (seeded `city.seed + 7`) and steep ground take
   away. A walled city (`enclosure`) is dense right up to its wall; `towns` add their own pull.
2. **Civic cores** (`civic_layout`): each node's plaza, rings, ring row of civic blocks, stoas and
   at most one monument, in the kit system. Organic nodes level only a summit plaza.
3. **Ways** (`ways`): each radial node gets an outer ring and `radials` avenues traced out of its
   core (`_trace`: straight or a spiral arm, steering towards gentle grades, stopping at the city
   bounds or another node); organic nodes get winding lanes and stairs; arterials join each node
   to its two nearest; a walled city adds a ring road, gate avenues and roads out; towns get
   lanes; a promenade runs along the shore.
4. **Blocks.** The region is the land inside `bounds` (inside the ring road for a walled city).
   The faces between the ways, minus the ways, the civic cores and the green corridors, are
   superblocks; `subdivide` cuts each across its long axis with a jittered cut (a local street)
   until it is block-sized for its density (`block_m`), contour cuts on organic hillsides.
5. **Markets** at avenue crossings of middling density, plus market nodes (forum courts) and town
   squares.
6. **Massing.** Below `urban_threshold` the city frays out; some blocks stay parks
   (`park_share`, more at the edge). Without a catalog `mass_block` places perimeter blocks,
   terraces or detached houses by density, storeys from `storeys` by density; with a catalog
   `city_types.mass` picks typologies per block. Harbour nodes get piers; trees line avenues
   and fill parks.
7. **Stats** (`city.stats`): footprint shares and the housing / monument checks.

Everything is drawn from one random stream (`np.random.default_rng(city.seed)`) in a fixed
order, so the same layout and seed give the same plan. `add_slots` turns the plan into greybox
slots (ways as draped ribbons, housing and streets per tile).

**Roll a city** (`asset_pipeline/city_roll.py`, Layout tab dice, `ap site roll`): six rule groups
(size, civic, density, streets, green, waterfront) are drawn from the seed in a fixed order
(`random_rules`), locked groups are copied from the current layout, `apply_rules` writes them and
the seed into `city`, and the plan is made with `sw_site.plan_city`. A random seed whose plan
fails the housing-share check is rolled again (up to 6 tries). `site/rolls.json` keeps the last
10 rolls (rules + seed); Previous / Next restore one and re-plan.

## Layout presets

### Done (2026-10-27)

- **`schema.LayoutPreset`** on `CitySpec.preset`, with `CitySpec.layout_seed` beside the city
  seed. Fields: `streets` (radial, grid, ring, organic, linear), `outline` (coastal, circle, strip,
  blob) and `outline_m`, `rings`, `grid_m` / `grid_deg`, `walls` (none, wall, wall_moat) and
  `moat_w`, `block_parks`, `falloff` (gaussian, linear, flat) and `falloff_scale`, `tall_at`
  (density, centre, nodes, waterfront) and `height_falloff_m`, `highways` (none, ring, radial,
  both) with `highway_w`, `highway_ring_m`, `highway_spokes` and `interchanges`.
- **"utopia"** is the default and is exactly the old generator: a layout.json without a preset
  loads as utopia, and the same seed gives the same plan. `tests/test_layout_presets.py` pins
  the plan digests of five test cities (plain, walled with a town, forum, hill town, typology
  catalog) to the ones the pre-preset code made. A real project was also checked locally:
  identical digests, and `city.json` and `city_plan.png` byte-identical after a re-plan.
- **Every field is honoured** in `city.py`:
  - streets: grid avenues on two axes with grid-aligned local cuts; ring roads; jittered,
    winding organic avenues with looser cuts; a linear spine with cross avenues. Grid and linear
    drop the radials and arterials, and every node keeps its own outer ring.
  - outline: the region and the density fade at its edge (towns are exempt); avenues stop there.
  - walls: `city.with_preset` adds an enclosure round the outline, and a moat (water, bridged
    where ways cross) for wall_moat.
  - block_parks: hashed per block, so it adds parks without shifting the random stream.
  - falloff: the per-node pull's shape and scale.
  - tall_at: `Fields.height_zone` drives the legacy storeys, and adds +-3 storeys for catalog
    buildings.
  - highways: a ring and/or spokes, `highway` slots built as flat ribbons, and `interchange`
    paving where highways cross each other or avenues.
- The **layout seed** feeds only the preset's own draws (`city.layout_rng`).
- **Named presets** (`layout_presets.PRESETS`): utopia, grid, ring-city, walled-town, linear,
  organic, metro.
- **Roll a city** keeps the layout's preset unless given one, records the preset and layout seed
  per roll, and Previous / Next restore them. There is also `ap site roll --preset/--layout-seed`,
  `ap site preset`, `POST /site/preset`, and the Layout tab's *Layout* / *Layout seed* /
  *Apply layout*.

### Next

- Districts per ring band: ring roads are roads only for now. District ids still come from the
  nearest node.
- Nodes outside the outline still get their civic core with no city round them. Options are to
  clip them or move them inside when a preset is applied.
- A strip outline with walls gets a circular wall. It needs a wall that follows the outline.
- Highways are draped on the terrain. They need grading, so they read flat on hills and cut or
  bridge, plus real interchange geometry (ramps). Highways also ignore the sea edge beyond the
  bounds margin.
- The roll does not pick a preset at random. It could become an optional rule group.
- Shots: the auto shots don't yet frame highways or a linear city's length.

## Terrain: generated vs heightmap (2026-10-27)

- **Generated**: `greybox.make_terrain(TerrainSpec)` builds a fictional coast and hills from the
  spec (extent, resolution, shore, headland, hills, bay). City mode then levels each node into a
  pad (`city.apply_pads`).
- **Heightmap**: `terrain.heightmap` points at a project-relative 16-bit PNG (decoded with
  `z_range`), e.g. the terrain build's `site/terrain_far/core.png`. `terrain.far` adds the far
  ring beyond the tile. `sw_site.terrain()` loads either kind and applies the pads; the greybox
  uses it. The plan uses `plan_terrain()`, which with a far build plans on the plain generated
  tile so the plan doesn't reshuffle with heightmap rounding.
- **Fixed**: `sw_site.city_shots` refused heightmaps ("city_shots needs a generated terrain"), so a
  roll (and `ap site plan --shots`) failed at the shots step on such projects. It now takes the
  store and, for a heightmap, samples the ground from `sw_site.terrain(store, layout, write=False)`,
  the same heights plus pads the build uses. Generated terrain is unchanged.
  Test: `test_city_roll.py::test_a_roll_on_a_heightmap_terrain_places_its_shots_on_the_heightmap`.
