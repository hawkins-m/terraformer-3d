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
- **Named presets**: see "Twenty presets" below.
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
- Shots: the auto shots don't yet frame highways or a linear city's length.

### Twenty presets (2026-10-27)

`layout_presets.CATALOG` holds name, one-line description (shown under the Layout picker) and
preset. Three names were renamed and still resolve as aliases (`ALIASES`): metro became
**classic**, ring-city became **concentric**, walled-town became **walled-castle**.

| Preset | What it is |
|---|---|
| utopia | the generator as before presets (default for every existing layout) |
| classic | an ordinary modern city (default for new projects): grid, tight tower downtown, mid-rise ring, wide suburbs, ring + radial highways with interchanges, industry by a highway |
| grid | plain grid, grid-aligned blocks, tallest in the middle |
| manhattan | a grid strip along the coast, long avenues and short cross streets, two tower clusters |
| park-grid | grid blocks separated by parks (checkerboard) |
| suburbia | winding streets, cul-de-sacs, low houses, radial highways |
| garden-city | wards between green rings, low and leafy |
| concentric | fantasy rings within rings round the centre |
| walled-castle | inner ward wall round the keep, outer wall and moat, ring lanes between |
| star-fortress | walled ring with pointed bastions and a moat, grid inside |
| linear | one spine along the coast with cross avenues |
| organic | winding avenues in a wobbly outline |
| canal-city | canals along the coast through a grid |
| river-town | a river winding from the hills to the sea past the centre |
| harbour-port | dense waterfront, many piers, docks and warehouses by the harbour |
| island-city | a wobbly island, sea all round, shore promenade |
| hill-town | every quarter on a summit, winding lanes and stairs, terraced slopes |
| cliff-town | contour terraces down a steep coast |
| market-town | two roads crossing at the market, compact |
| campus | a green mall on the main axis, low halls on a grid, lawns |

New preset fields behind them (all honoured in `city.py`, all off in utopia):
- `downtown_m` / `tower_storeys`: tower-on-podium blocks, tallest at the civic core's edge.
- `suburbs_m`: house density all the way out to the outline.
- `cul_de_sacs`: a dead-end street and a turning circle (`culdesac` slots) carved into deep
  suburban blocks.
- `industrial` (highway / harbour) and `industrial_m`: shed blocks (`industrial` slots).
- `bastions`: diamond bastions (`bastion` slots) against the wall; the moat goes round them.
- `inner_wall`: a second wall (`innerwall-*` / `innergate-*` slots, typed wall / gate) round
  the main node, gated where ways cross it.
- `canals` / `canal_w`, `river` / `river_w`: water ways (`canal`, `river`), bridged by the roads.
- `outline: island`: `apply_pads` sinks the terrain outside the island and raises it inside;
  `Fields.inland` / `land`, the land region, the promenade and the piers follow the island's
  shore.
- `streets: crossroads`; `grid_ratio` (long grid blocks); `park_pattern: checker`;
  `park_rings` (the ring radii become green rings); `mall_w`.
- `organic_nodes`, `terraces`, `max_slope`, `max_storeys`, `piers`.
- `clear_overlaps`: drops buildings standing on a road, water or paving, or on another slot's
  building (the larger stays), and opens a wall where a way crosses it away from a gate.
  Every preset but utopia sets it. Utopia keeps its old overlaps (113 on the scratch city: tile
  seams through perimeter blocks, boxes poking into avenues). Turning it on there would change
  existing projects' plans and their locked frames.

Roll: the Layout picker's **Random preset** has a roll draw one from its seed
(`layout_presets.random_name`, own stream); the drawn one is saved and recorded per roll. New
projects (`sw_site.init(city_mode=True)` without a layout file) start on classic.

Checks: `tests/test_layout_presets.py` builds every preset (plan + greybox spec) on the test city
with no overlapping slots (its own checker, apart from `clear_overlaps`), and checks classic's
parts and each preset's features. On the scratch project all 19 non-utopia presets plan with
0 overlaps. Previews of utopia, classic, manhattan, star-fortress, canal-city and garden-city are
in the scratch project's `site/preset_previews/`.

### Next (presets)

- Utopia's overlaps: fix them only with the user's OK. Locked frames depend on its plan.
- clear_overlaps drops buildings (30–290 per city). Trimming or re-fitting them would keep more.
- Catalog (typology) cities: downtown towers and sheds are plain boxes, not catalog typologies.
- Island presets on a layout with an enclosure or towns give a wall and greenbelt that run into
  the sea, and towns across the water vanish.
- Bastions and the inner wall are boxes on a circular wall. A real star trace would bend the wall.
- Industrial sheds need their own catalog look, and suburbs need bigger lots and driveways.

### A preset owns the whole city shape on a roll (2026-10-27)

Before this, a preset only changed streets: `ap site preset` / *Apply layout* left the project's
wall, green belt, districts and satellite town in place, so the scratch project's six previews all
showed the same round wall and districts. Now:

- **`city_shape.py`** has a recipe for every preset but utopia: the outline and its size, the wall
  (kind, radius, moat, green belt, trace: circle or **star**), the districts (ids, roles, landmark
  monuments, places as angle and distance from the centre, core size, square, civic ring, stoas)
  and the satellite towns (how many, how far, names).
- **A roll with such a preset** (`city_roll.roll`) places that shape on the plan's ground
  (`sw_site.plan_ground`):
  - The centre sits at the recipe's distance from the coast; an island goes offshore.
  - Every district core sits on land, above the sea and off steep ground, inside the bounds and
    the outline, apart from the others. "high" districts take the highest ground near their spot;
    "coast" districts sit by the water.
  - Towns sit beyond the city on land, and the bounds grow to take them in.
  - The project's own civic sites are dropped, because they were placed for the old shape.
  - The wall, towns and sites are stored per roll, so Previous / Next restore them.
- **Classic** has no wall, no green belt and no forum. Its districts are downtown (a plain square,
  no arcades, no stoas, with towers round it), midtown, eastside, westside and a harbour. It has
  ring and radial highways, a mid-rise ring, suburbs, sheds by a highway, and two satellite towns.
- **Manhattan** is a narrow strip along the coast: the sea on one long side, and `channel` river
  ways along both long sides wherever there's land.
- **Star-fortress** gets a star trace (`Enclosure.shape = "star"`, `points`, `point_m`). Its bays
  follow the sides, gates sit at the inner corners, and the moat and green belt follow the star.
- **Island-city**: `preset_terrain` raises the island and sinks a strait of sea
  (`ISLAND_STRAIT_M`) round it. The mainland stays land and gets a "mainland" town.
- **"Keep current outline and districts"** (a tick box in the roll, off by default;
  `--keep-shape`; `keep_shape` in the API) keeps the size, civic and waterfront groups, the
  wall, towns and sites, and the layout's own `SHAPE_FIELDS` (outline, walls, water). The roll
  then takes only the new preset's streets.
- **Utopia** (and a roll with "Civic nodes" kept) rolls as before: the rolled districts, the
  project's own wall and towns. A new fit pass (`city_shape.fit_nodes`) moves any district
  out of the sea and leaves the rest alone.
- **Heightmap terrain** keeps its waterfront rules on every roll. The heightmap holds the coast,
  and before this a roll's rules moved the plan's coast away from it.
- **Existing projects** keep utopia unless rolled with another preset. `ap site preset` /
  *Apply layout* still change the preset alone.

Checks: `tests/test_city_shape.py` rolls every recipe preset on a walled test project with a
town. It checks the preset's own districts and towns, that every district core is on land, and
the wall (or none). It also checks classic's parts, Manhattan's strip and water, the star trace,
the island's strait, keep-shape, utopia, history restore, heightmap coasts, and the API and CLI.
Previews from rolls through a second app instance are in the scratch project's
`site/preset_previews/`: `flat-*` (a blank flat test project: utopia, classic, manhattan,
star-fortress) and `scratch-*` (walled-castle, suburbia, island-city, canal-city). The earlier six
street-only previews were removed.

### Next (shape)

- The catalog's district rules (mix, palette) are keyed by the old district names. Renamed
  districts fall back to an even housing mix. A preset could bring its own catalog districts.
- Manhattan's channels are water ribbons, not cut into the terrain. Rivers on slopes sit on the
  ground.
- An island has no bridge to the mainland.
- Recipes place districts by angle and distance. They could also read the terrain's valleys and
  ridges.
- Locked shots keep their cameras after a shape roll, though their frames show the old city.

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
