# World and city

World and city mode: greybox and passes, concept frames and their control settings, city planning, typologies, walled city, locked shots. Moved from CLAUDE.md on 2026-10-21; rules here still apply when you work in this area.

## World mode: greybox and shot passes (verified 2026-10-05)
- Headless Blender 5.2 (CPU) builds an ~80-slot / ~4.4k-piece greybox in about 4 s.
  Pieces with the same primitive share one mesh, so kit pieces stay instanceable.
- The data passes are exact with Cycles on the CPU at 1 sample, with:
  - a box filter of width 0.01;
  - `dither_intensity = 0`;
  - the Raw view transform;
  - an emission-only material override.

  The ID pass then decodes with 0 unknown pixels, and the ID and depth hit masks agree
  pixel for pixel. Any of AA, dither or a view transform blends slot colours into
  colours nobody has.
- Depth is stored as 16-bit camera Z over 10 km (15 cm steps): the sea plane reaches
  about 5 km. Sky is 0.
- About 3.5 s per shot for all four passes at 1344x768.
- A rebuild always changes the .blend's hash, even from the same layout, so every shot's
  passes show as stale afterwards. Re-render them.
- Kit modularity: colonnades snap to whole modules, and a stoa's bay angle is module /
  radius. Without that, every building had its own entablature length (no reuse).

## Concept frames (world mode) findings (2026-10-06)
- ControlNet Union Pro 2.0 (`flux1-dev-controlnet-union-pro-2.0.safetensors`) and the
  BFL `flux1-depth-dev-lora.safetensors` both load in ComfyUI 0.24 with core nodes.
  Union needs the VAE input on `ControlNetApplyAdvanced`. About 35 s per 1344x768 frame
  at 28 steps on GPU 1.
- A/B (`scripts/diag/frames_ab.py`, 3 shots: a domed rotunda at eye level, a wide coastal
  aerial, a curved colonnade beside a garden; same seed and prompt). Edge match
  (chance-corrected, see `s0_frames.edge_match`) means:

  | Variant | Match | Look |
  |---|---|---|
  | Union depth 0.8, end ≥ 0.6 (+ canny 0.4) | 0.88–0.93 | Greybox shows through: flat grey ground, plain masses, desaturated; the sea became a concrete wall |
  | Union depth 0.6/0.6 + canny 0.3/0.5 | 0.79 | Layout exact, but surfaces flat and garden turned to paving |
  | **Union depth 0.6/0.6 alone** | 0.67 | **Best**: layout and scale hold; planting, sky and light come back. The default. |
  | Union depth 0.4 (any end) | 0.50–0.72 | Style good, layout and scale drift (a rotunda shrank behind an invented arch) |
  | Depth LoRA 0.65 / 0.85 | 0.17 / 0.37 | Redesigns or relocates buildings: unusable for one consistent place |

  Canny edges from the greybox are what flatten surfaces: they ask for the greybox's
  plain planes.
- Cross-shot references (`--ref`, an approved wide frame as extra Redux image at total
  +0.08), tried on two medium shots, same seeds:
  - they carry palette, light and planting across shots (the mediums took the wide
    frame's warm backlight and planted parterres: clearly "the same city");
  - they also carry the reference's sky: a hazy, sun-filled sky turned a large dome
    into a translucent glow, and a smaller invented dome took its place.

  Keep it opt-in. Try a lower strength (≈0.04) or a reference with a clear sky before
  making it a default.
- Material wording (A/B 2026-10-06, 2 medium shots x 3 seeds, wording only):
  "board-formed concrete" draws wood: slatted screens, timber fins, plank-streaked
  walls. "Honed pale stone/travertine" gives banded, streaky surfaces. Precise wording
  per material (`SiteLayout.materials`), e.g. "polished white Carrara marble, smooth
  seamless stone with fine soft grey veining, crisp sharp machined edges", gave veined
  marble and clean ashlar. The style text must drop the vague words too: it is appended
  to every prompt. Cost: the frames come out whiter and more sterile (less warm brick).
- A material's `ref` is Redux masked to its slots (id pass; `ConditioningSetMask` +
  `ConditioningCombine`). Each masked cond is one more model evaluation per step; the
  only run so far (one ref) took 91 s including the model load. Not yet A/B'd: no
  moodboard images.
- Wide aerials read a plateau's edge (a depth step around the city) as a circular ring
  wall, and a sparse layout makes wide shots look like a scale model.
- The edge score must be chance-corrected: raw recall gave random noise 0.96. Sobel
  edges at the top 12%, dilated 2 px, cover most of a busy image.
- Shell gotcha: `pgrep -f`/`pkill -f` with a script path also match the calling
  shell's own command line (a wait loop never ends; pkill kills itself). Match on
  something the caller doesn't contain, or use PIDs.

## City mode (verified 2026-10-06)
- `SiteLayout.city` (`asset_pipeline/city.py`): civic nodes, terrain-steered avenues,
  organic blocks, instanced massing. `ap site init --city` (neutral starter city) and
  `ap site plan PROJECT` (plan image + area report, no Blender, ~1 s).
- Measured at 5 x 3 km on 8 km terrain (6 nodes): ~7.6k buildings, ~10k trees, 468
  slots, 19.8k pieces. Greybox build 11 s; 17 shots' passes 155 s (~9 s each, CPU);
  export 208 s (251 GLBs); UE import 1-1.5 min fresh, 8 s unchanged.
- Housing is a scaled unit box (`scale` on the piece): one mesh for every building, so
  Blender, the export and UE instancing stay cheap. Draped pieces (ribbons, lawns) are
  unique, so the build merges them into one mesh per slot: 496 -> 186 GLBs on the
  starter city, export 314 s -> 115 s.
- Slot ids made file-safe must stay unique: tiles are `x<i>y<j>` (`-3_-1` and `3_-1`
  collided as asset ids).
- Density: `1 - prod(1 - exp(-(d / (falloff * weight))^2))` over the nodes, plus a
  waterfront bonus and noise, minus steep ground. With a 900 m falloff the whole city
  was dense; 650 m gives cores, terraces and a fraying low-rise edge. Classes: >= 0.6
  perimeter courtyard blocks, >= 0.4 terraces, below detached houses.
- Spiral avenues need a cap on their total turn (75 deg), and arterials must aim at the
  bearing to their target: otherwise both coil or loop near a node's pad.
- Auto shots: wides look down ~13-18 deg (lower ones showed the city as a sliver on a
  coastal plain); the aerial shows the radial plan; the tight steps back by the
  monument's longest side.

- First city frames (Union depth 0.6/0.6, 2 per shot, 2026-10-06): the wides read as a
  large coastal city. Eye-level node mediums follow the layout (edge match 0.45-0.78) and
  the precise material words hold (marble, no wood). Views from far above follow it
  poorly: raised node mediums 0.00-0.38 and the aerial 0.06-0.12. Flux keeps the
  "radial city" idea but invents its own plan and megastructures (oval stadia, curved
  slabs), because a near-top-down depth pass is almost flat. Per-view control settings: see "Concept frames: views from above".

## Concept frames: views from above (2026-10-09)
- A shot's view comes from its camera (`s0_frames.view_of`): aerial if it looks down more
  than 35 deg, raised if a medium or tight looks down more than 3 deg, else level. Wides
  at 13-18 deg down hold their layout (0.55-0.58) and stay level.
- Why raised views drift: the plain depth image is inverse depth over the whole view, so
  it's mostly the ground's near-to-far ramp, and 10-30 m of buildings are faint bumps on
  it (on the aerial the radial plan is almost invisible). `sw_shots.depth_relief`
  removes the ramp with a masked blur and gives half the range to the local relief. A
  plane's inverse depth is linear in the image, so the blur keeps the ground flat grey;
  the blur's border must be extended linearly (odd reflection), otherwise the frame's edge
  turns into a false relief band that eats the scale.
- A/B (`scripts/diag/frames_ab.py --set above|above2`, 3 raised mediums + the aerial,
  2 seeds, wording only), mean edge match on the raised shots:

  | Variant | Raised | Aerial | Look |
  |---|---|---|---|
  | default depth 0.6/0.6 | 0.16 | 0.08 | Invents its own plan (an avenue with lettering) |
  | plain depth 0.8/0.8 | 0.45 | 0.10 | Pine canopies became lawns; a plaza flooded |
  | depth 0.6/0.6 + canny 0.3/0.4 | 0.49 | 0.11 | Good layout on one shot, surfaces flatter |
  | relief 0.6/0.6 | 0.22 | 0.13 | Aerial keeps colour and a city look |
  | **relief 0.75/0.75** | **0.49** | 0.19 | **Raised default**: layout held, real buildings; aerial a white scale model |
  | relief 0.75/0.75 + canny 0.3 | 0.71 | 0.18 | The greybox shows through (white boxes, grey plaza) |
  | relief 0.75 to 0.5 | 0.35 | 0.15 | Drifts; aerial still white |

  Defaults (`schema.DEFAULT_VIEWS`): raised = relief 0.75/0.75, aerial = relief 0.6/0.6.
  The harbour raised shot still drifts at the default (0.10/0.31); the aerial is unsolved.
- Masked references cost one model evaluation per step each, whatever their size: 34 s a
  frame with none, ~76 s with one, ~116 s with two. They're skipped below 5% of the frame
  (`FrameSettings.min_ref_coverage`).

## Typology catalog (city mode) findings (2026-10-07)
- Shares must be measured by footprint, not by building count: a row of narrow
  townhouses counted as 56% of "buildings" while covering 22% of the ground.
- With only the district mixes, 2-3 types took 40-60% of a district. Two fixes brought it
  near the 22%/40% caps:
  - weights back off (x0.15) once a type nears its cap, by footprint;
  - the fill around a single civic building is a weighted pick, not the strongest type.

  Diversity per district then went from 0.7-1.5 to 1.5-1.9 (Shannon).
- A 5 x 3 km city with 33 typologies: ~9.4k buildings, plan 2.5 s, 785 slots, greybox
  build 30 s.
- A typology with one roof and one facade can only vary by storeys. At its minimum storey
  count it must step up, or identical runs of 6-9 appear.
- Prompts are not regional: a landmark's material words (a warm stone colour) turned the
  whole raised frame that colour, not just the landmark. Only the masked Redux reference
  stays local. Prefer the masked ref plus neutral district words for colour that must stay
  on one building.
- Eye-level from ~120 m, a lagoon in front of a landmark is a sliver in the depth pass;
  Flux then invents a rectangular pool. Raised shots show it.
- `pkill -f` / `pgrep -f` on a command line match the calling shell (see above): stop
  servers by PID (`ss -ltnp`).
- UE 5.8 Python: `MaterialExpressionSaturate`, `Constant3Vector` (`constant`) and
  `LinearInterpolate` ("A", "B", "Alpha") build in the commandlet with `-nullrhi`;
  `get_material_instance_*_parameter_value` reads the instance's current value, which is
  how a value tuned in the editor is detected (metadata tag holds the pipeline's last
  write).

- Height variation (2026-10-08): with storeys 4-10 and +-2 jitter, a perimeter block's
  height spread (std / mean) lands around 0.21-0.30, even though it visibly runs from 4
  to 10 storeys. A 0.3 minimum flags about a third of blocks; corner emphasis (+1-2
  storeys) barely moves it. The district height field doesn't help within a block: it's
  smooth over ~700 m. Treat ~0.2 as "varied" for mid-rise blocks.
- Core blocks around a civic node are wedges (35-70% of their convex hull): a single
  building centred on the bounding box falls outside. Centre it at the block's most
  interior point (`polylabel`) and check type sizes against the inscribed circle.
- Plot merging: STRtree neighbours include blocks across avenues (19-24 m); only join
  blocks within street_w + 1 of each other. A local street is one long cut line across
  several blocks: clip it to the merged outline rather than testing its midpoint.

- Frame review round 2 (2026-10-08):
  - Style drift (frames reading as suburban California, North African or Middle Eastern)
    came from the words: "warm ochre / pale rose render", terracotta pitched roofs as the
    default roof, "citrus", "canvas awnings", and lollipop street trees (read as palms).
    Flux has no negative prompt here, so name what you want instead: travertine, marble,
    flat roofs with crisp cornices, umbrella pines, cypresses, olives.
  - A hill node levelled as one 220 m pad (the default pad) reads as a flat ring city;
    level only the summit plaza and let the slope stay.
  - Contour strips on a hill come out a few degrees off the local slope (the cut follows
    the parent block's slope). Terraces must follow the strip's own axis, or a 40 m
    terrace won't fit a 15 m strip, and the block stays empty.
  - A street cut can cross a block in several pieces: give each piece its own id.
  - **Arched walls were solid until 2026-10-08.** A notched outline (arches cut from the
    bottom edge) made by `bm.faces.new` + triangulate came out with overlapping triangles
    over the openings: the front faces covered twice the wall's own area, and every arch
    (rotunda sides, gates, aqueducts, arcades) was a solid wall in all passes. Build such
    walls from convex pieces (piers plus a comb of trapezoids over each arch);
    `tests/test_world.py::test_arched_walls_have_open_arches_in_blender` checks the area.
  - Depth only shows detail near the camera: a 6 m arcade walk at 35 m is under 2% of the
    shot's inverse-depth range (invisible), and canny needs an 8% relative depth step. An
    arcade reads at 5-20 m: look along it, not across a court at it.
  - Anchor A/B (6 moodboard scene images, total 0.05, plus two masked references): about
    310 s per frame vs 117 s without the anchor (vs ~48 s with no references at all).
    The anchor added ornament and palms and lowered the edge match (0.15 vs 0.40, same
    seed): left off.
  - Palms came from the words, not the trees: "sunlit coast" with sandy stone gave palms in
    every market frame, even with no palm-like silhouettes. "clear central-Italian light"
    plus "no palm trees" removed them on the same seeds, with olives in the planters
    instead. Flux's T5 handles that short negation.
  - Check what a frame shows before using it as a reference: the user's match numbers and
    descriptions for two frames were swapped. Go by the description.

## Walled city and locked shots (2026-10-10)
- `CitySpec.enclosure`: blocks only inside a ring road behind a circular wall (archwall
  bays of one mesh, on land only, open at the gates), density floored inside (no fraying
  edge), a greenbelt outside in hole-free wedges (a lawn is drawn from its outline: an
  annulus would cover the city). `towns`: small settlements with lanes and a square;
  `terrain.bay_*` puts a bay between. A 2 km ring with 6 nodes: ~2.3k buildings, build 14 s.
- Steep hills inside a compact ring leave bare patches (slope cut); hill_height 170 m over
  500-3200 m inland filled them.
- The aerial of a walled city is a high oblique (`aerial_pitch`, 35-42 deg); its distance
  must fit the near and far edges in the frame's height, not just the width
  (`city.aerial_distance`). A wide looking down >= 28 deg is an "aerial" view.
- `ShotSpec.locked`: approved shot, never regenerated nor re-placed (`ap frames lock`).
- Concrete wording A/B (2 shots x 2 seeds, wording only): smooth fair-faced, "board-marked
  cast concrete, cool grey, fine horizontal formwork lines, no timber" and "porous
  concrete" gave near-identical frames per seed, none with wood (the round-1 wood came
  from "board-formed"). The cream cast comes from "pale"/"ivory"/"warm" words, not the
  material name.

## Raised shots: realism A/B and the forum centre (2026-10-10)
- Raised frames read as white CG scale models. A/B (`scripts/diag/frames_ab.py --set
  realism|realism2`, 4 raised mediums x 2 seeds, an approved eye-level frame as target):

  | Variant | Edge match | Look |
  |---|---|---|
  | relief 0.75 to 0.75 (old default) | 0.69 | Scale model |
  | **relief 0.75 to 0.5 / 0.6** | 0.48 / 0.54 | Only lever that helps: haze, hills, weathering; palms can creep back. Default now 0.55 |
  | own locked medium as ref at 0.10 / 0.15 | 0.68 / 0.66 | No visible change |
  | + "dense dark-green umbrella pine canopies, seen from above" | same | No change: crowns stay grey dishes |
  | img2img realism pass (`s0_frames.refine`, no control) 0.25-0.5 | -0.03 to -0.1 | Stains and sharper detail, harsher contrast; never softer or hazier, never less model-like |

  Smooth dome crowns seen from above read as dishes or mushrooms whatever the words
  (from the side they read as pines). Seen from above, the scale-model look comes from the
  greybox itself (big blank roofs, smooth crowns), not the sampler.
- Pine crowns are domes 0.32 x their width (were 0.24 x the height, ~0.2 x the width:
  discs in wides). Trunk 1.4 m.
- `CivicNode.centre = "forum"`: an enclosed rectangular court (0.83 R x 0.5 R), four
  12 m portico ranges (shops behind, two orders, attic), gate arches where each radial
  crosses a range (6 radials: both short ends, +-24 m on the long sides), a fountain; a
  market node fills it with a stall grid. Its own random stream, and the loose market's
  draws still taken, so the rest of the plan (other markets, every tree) stays put.
  - The eye-level camera must stand clear of the stall rows (a hut 5 m away fills the
    frame) and outside the gate piers: in the court just in front of the end colonnade,
    beside its gate, looking diagonally at the far side's gate.
  - The masked landmark ref makes each frame ~100 s.
- `ap frames lock SHOT --greybox`: camera and greybox locked, no frame (wides: Flux
  can't resolve a whole city at frame size; they render in UE from the real assets).

## Roll a city (2026-10-27)
- `asset_pipeline/city_roll.py`: groups size, civic, density, streets, green, waterfront (city spec
  plus the terrain's coast); `random_rules(seed)` draws every group in a fixed order, so a kept group
  doesn't shift the others and seed + keeps reproduce a roll. Ranges stay near the starter city's;
  the shoreline follows the city's half-height so the city reaches the water; nodes are kept apart
  and on land. The seed is also `city.seed`.
- A roll saves layout.json, re-plans with `sw_site.plan_city` (UI: a `site.plan` job tagged roll, so
  the Re-plan busy state and the site lock apply) and re-places the auto shots (`set_city_shots`:
  locked shots and shot edits stay). A random roll that fails the housing-share warning tries up
  to 6 seeds; a typed seed is kept. History: `site/rolls.json` {rolls (last 10: seed, rules, keep,
  warnings, at), pos}. A project with its own layout (no city spec) can't roll; one with no layout
  starts from the starter city.
- The dice box (`#city-roll`, city.js) sits after the catalog, away from Update / Re-plan. Its
  confirm is a plain `dialog()` with Cancel as the default button (focused: Enter cancels, Esc too).
  Its new-project hint is `.roll-hint`, not `.start-hint` (renderStartHint removes every
  `.start-hint` on each redraw).

## Layout presets (2026-10-27)
- `CitySpec.preset` (`schema.LayoutPreset`, named ones in `layout_presets.py`) drives the city's
  overall shape; `city.py` honours every field. Default `utopia` = the generator as before presets,
  pinned by plan digests in `tests/test_layout_presets.py` (keep them passing: any change to the
  utopia path must leave those digests alone). Preset-only draws use `city.layout_rng`
  (`CitySpec.layout_seed`), never the city seed's stream; parks between blocks use a hash per block.
- Preset walls become an `Enclosure` via `city.with_preset` (plan, shots, terrain plan/build, areas);
  never saved, so switching back to `walls: none` leaves no wall behind.
- New slot types: `highway`, `interchange` (flat paving), `moat` (water piece `moat-water`), `culdesac`,
  `river`, `canal` ways, `tower`, `industrial`, `bastion`; inner walls are `innerwall-*` slots typed `wall`.
- Presets other than utopia set `clear_overlaps`; utopia keeps its old overlaps on purpose (locked frames).

## Classic is a coastal metro (2026-10-30)
- `LayoutPreset.metro` (classic only) routes density, grid angle, arterials, lakeshore, river, green,
  industry and bridge decks through `city_metro.py`; `city_shape.metro_shape` puts downtown on the coast
  at the river's mouth (the terrain's river if it has one), grows the bounds to the shore, no towns.
  Every gate in `city.py` is `if pr.metro`, so other presets are untouched: `tests/test_classic_metro.py`
  pins every other preset's plan digest (small city, coastal plain, other terrains) and greybox slots.
- Projects saved with the old classic keep its fields (no `metro`) and plan as before; picking classic
  again (or a roll with it) gives the metro. Same slot types: bridges are level `avenue`/`highway` decks.
- Tuned (2026-10-30): arterials every `grid_m` 1600 m, local streets every 200 m (100 m across downtown
  and its mid-rise ring) as `local-*` street ways, stopping at the lakeshore; `city_metro.mass_block` packs
  frontage round rear yards (mid-rises, rowhouses, townhouses; detached houses below `ROW_D`); the tower
  cluster is `downtown_r` = 0.5 x the density scale, tallest at `peak` (towards the river mouth), then a
  mid-rise tower ring; `trim_stubs` cuts dead ends back to 150 m past the last building. About 3x the
  buildings: a scratch metro is ~6-8k buildings. Rivers are coloured as water in `blender_greybox.COLORS`.
- Third pass (2026-10-31): local streets every 100 m along the coast and 200 m across it (Chicago-like
  long blocks); middle-ring blocks are two rows back to back with an alley (`_rows`, `ALLEY_M`, `YARD_M`);
  downtown blocks pick full-block / podium + slim towers / L / stepped / plaza / lots, a landmark or more
  by the river mouth; `cap_towers` keeps at most `TOWER_CAP` (500) towers over 12 storeys; a suburb floor
  (`SUBURB_Q`, noisy) spreads 1-2 storey detached houses out past v1's extent. ~4.5-8k buildings,
  200-340 towers on the scratch seeds.

## Hand edits follow into Unreal (2026-10-30)
- `site/build/built_slots.json` (written by `ap site build`, or by a Re-read of an unedited greybox
  built before it existed) is the greybox as built; `sw_site.slot_edits` diffs every slot against it
  on Re-read (`greybox.json` `slot_edits`). Older projects fall back to `build/spec.json` (no bbox:
  a deleted slot's builds are then held as "no greybox slot").
- `slot_delta` D = S_now @ C @ S_built^-1, C the move all of a slot's child meshes share (a viewport
  click selects meshes, not the Empty); partial piece moves count as none. Below 5 cm / 0.1 deg / 1 %
  D is identity: a re-saved .blend shifts matrices by ~1e-5.
- Typed buildings are matched to slots by the *built* bbox, then `city_library.follow_slots` moves
  them with D (placement `scale` along their own axes; `world_matrix` applies it). Duplicates are
  `ap_source`-tagged by extract (else the `-<n>` suffix); copies are keyed `<key>_<slot>`, same GLB.
- Measured on a ~490-slot city copy: plan 11 s, Re-read 7 s; untouched placements agree within 1 mm
  (yaw may wrap by 360 between runs).


## City sizes and the background region (2026-10-31)
- `CitySpec.size` (`city_size.py`): standard / large (~8 km across) / metro (~14 km). A size scales the
  standard city's bounds, nodes and terrain (coast, hills, mountains, river, plateau x k; hill radius and
  meander x sqrt k), grows the tile to the bounds + 1.5 km (2017 samples up to ~6.5 m spacing, else 4033)
  and refits its z range, then reshapes (the preset's recipe with the city seed) and re-plans. The
  standard bounds, terrain and nodes are kept in `sized_from`, so standard restores them exactly and a
  resize never compounds. New CitySpec fields are `exclude_if` their defaults: a standard layout.json,
  spec and city3d.json are byte for byte as before (`tests/test_city_size.py` pins classic too).
- Gates are `k != 1` (`city_size.factor`): `city.with_preset` scales outline_m, highway ring, downtown_m,
  suburbs, industrial radius and adds round(k) - 1 highway spokes, once (`sized_from["preset_scaled"]`:
  the plan's own layout isn't scaled again); classic's large parks x k^2 (x sqrt k in size), tower cap x k.
  Most of classic already follows the bounds (density scale, downtown radius, river forks, arterials).
- Re-running `site size` at the same size used to scale the already-scaled nodes again (the city lost
  its housing): the same size now changes flags only.
- `city_background.py`: the far terrain is the project's own generator over the whole region at ~30-35 m
  (its noise is grid-indexed, so it only matches the tile at the edge: the tile's edge heights fade into
  it over 1.5 km), hills rising to 260 m at the region's edge; grid lines meet the tile's edge so the
  greybox's `npz_mesh` cut-out lines up. (Its first version cleared the bounds + 350 m: an empty ring;
  see "The background starts at the city's edge" below.)
- Greybox collection instancing (large / metro): Blender 5.2 refused a collection as a Geometry Nodes
  modifier input (`id properties not supported`), so each mesh has its own node group copy with the
  Collection Info set on the node. Cycles gives GN instances the instancer's object colour: the ID pass
  still decodes per slot. The export follows `ap_instances` to the prototype mesh (else every point cloud
  became a GLB).
- Unreal, World Partition (large / metro, level `<Name><Size>`):
  - a commandlet loads none of a partitioned level's actors: the import found nothing and made every slot
    again (3,386 actors after three builds). `ap_wp.load_all_actors` loads them by their actor descs
    (`get_actor_descs` is None on a plain level). Changes to existing actors aren't marked dirty there and
    `save_current_level` skips their external packages: the import saves them explicitly.
  - HLODs: a layer made by hand was "invalid" (not in the partition's HLOD setup); use the level's own
    `<Map>_HLODLayer_Instanced`. Slot actors (plain Actor + ISM) were never HLOD-relevant: they were
    movable; static mobility on the root and the instance components fixed it (0 -> 724 HLOD actors on the
    large scratch city). The template's Merged parent layer bakes 8192 px textures per cell (89 of 724 in
    29 min): the import clears the parent so only instancing HLODs build. The builder crashes with
    `-nullrhi`; it renders (adapter 0).
- Measured on the scratch classic city (coastal plain, seed 3), large and metro with their background
  region; Unreal builds on a fresh UE project, greybox build includes the plan and extract:

  | | Standard | Large | Metro |
  |---|---|---|---|
  | across / terrain tile | 4.1 / 8 km | 8 / 11 km | 14 / 17 km |
  | buildings (plan) | 4,878 | 18,243 | 59,150 |
  | greybox pieces / objects / unique meshes | 19.3k / one per piece / 9 | 79k / 1,312 instancers / 12 | 173k / 3,435 instancers / 12 |
  | greybox build (Blender alone) | 13 s, 16 MB | 30 s, 50 MB | 89 s (5.6 s), 79 MB |
  | Unreal build (`ap ue build`) | 2.4 min | 2.8 min | 21.6 min (import 17.7: 1,053 GLBs) |
  | level | 28 MB .umap | 12 KB .umap + 85 MB actors, 407 HLODs | 12 KB + 199 MB, 955 HLODs |
  | editor over downtown (GPU 1) | 120 fps (cap) | 120 (cap) | 120 (cap), GPU ~85 % busy |
  | 3D viewer, orbiting (R9700, Vulkan) | 60 fps (vsync) | 60 | 60, also the whole city |

  Metro's Unreal time is the import of 1,053 stand-in meshes, nearly all unique draped pieces (streets,
  fields, ways: one GLB per slot), not the instances; it is labelled experimental in the UI
  (`city_size.EXPERIMENTAL`). The editor's own 120 fps cap holds with the cvars off; GPU load is the
  better gauge. Software GL (swiftshader): metro's viewer 1.5 fps at the whole city, 3.0 with its LOD.

## The background starts at the city's edge (2026-11-01)
- `city_background._Edge`: the city's blocks, parks, markets, buildings, pads and civic cores on a 20 m
  raster (scipy EDT): distance from its built edge; plus its streets and trees: distance from anything
  of it (nothing within 25 m). The bounds' box isn't used: the large scratch city's blocks fill only
  part of it (stepped outline), and the old bounds + 350 m clearance left a bare ring 0.5-1.5 km wide.
- Bands by distance from that edge x (1 + 0.35 noise): fringe < 900 m (houses along every road, small
  farms of 20-60 m strips, copses), belt < 4.2 km (farm estates, woods between, rare meadows), then
  forest with clearings; farms there only within 1.5 km of a road or river on flat ground.
- Farmland: Voronoi estates (650 m seeds), each lined up with the nearest road / river / city street
  (one in four turned 90 deg), rows of strips of mixed widths, one row in five turned across; hedges
  (one stretched dome per <= 220 m) or tree lines on some long sides. An estate whose seed lies in the
  city or the sea still gets its strips: each strip is tested on its own (else bare blobs at the edge).
- Lanes grow in 50 m steps (heading noise 0.6 x turn + N(0, 1.2 deg)) off the highways every 0.9-1.9 km
  and out of the city's edge every ~900 m; they stop on water, the city, a village or the region's edge,
  and join another road (100 m cells). Farmsteads (house + 1-2 barns, 48 m yard) along lanes and highways
  past the fringe. Villages: a main street off the highway, 2-4 side streets, houses along both, a hall.
- Cost (Unreal assets are one per slot and draped piece): fields per 3 km slot (was 2 km), lanes and
  village streets per 4 km slot, houses / barns / hedges on the existing box, gable and dome meshes.
  Scratch large (classic, seed 3), Unreal on a fresh UE project:

  | Large | before | after |
  |---|---|---|
  | fields / forest clumps / hedges | 1,845 / 18.7k / - | 5,040 / 29.7k / 220 |
  | villages (houses) / farm buildings / lanes + village streets | 7 (160) / - / - | 10 (1,238) / 422 / 59 + 29 |
  | background plan | 0.4 s | 3.3 s |
  | slots / Unreal assets / instances | 834 / 471 / 37.8k | 896 / 439 / 52.1k |
  | greybox build | 34 s | 38 s |
  | Unreal build, fresh project, warm DDC | 2.7 min (import 90 s) | 2.6 min (import 97 s) |
  | first build after the change (new meshes not cached) | - | 3.9 min (import 161 s) |
  | viewer (R9700): scene built / frame orbiting | 82 ms / 0.3 ms | 105 ms / 0.3 ms |
- Measured bare land (nothing on it, by distance from the edge) after: 0-300 m 29 % (yards, meadows),
  300-900 m 12 %, 0.9-2 km 10 %, 2-4.2 km 8 %, beyond 7 % (lattice gaps).

## Merge ground surfaces (2026-10-31)
- `ground_merge.py` (project.json `unreal.merge_ground`: off / auto = metro only / on): the export joins each
  slot's own draped mesh (`<slot>.<piece>`) of street, road and field slots into ~1 km tiles, one mesh per
  tile and surface (streets, fields with crop and fallow together, each road type apart: the tint is the
  slot type's, paving dressing goes by slot type). Grouping by piece instead made more meshes, not fewer
  (fields are already one mesh per 2 km slot). Parks (382 lawns on the metro) aren't merged.
- Metro (scratch, seed 3): 1,053 -> 731 assets, 648 pieces of 470 slots -> 326 tiles; each tile's faces and
  world bounds match its pieces. Standard and large manifests are byte for byte as before under auto.
- **The derived-data cache decides Unreal build times**, not the asset count alone. `-ddc=Cold` (an empty,
  flushed Zen sandbox per process; via an `AP_UE_EDITOR_CMD` wrapper) vs the warm shared cache:

  | Metro | cold | warm |
  |---|---|---|
  | merge off | 32.8 min (import 1,044 s, HLODs 808 s) | 7.6 min (import 312 s, HLODs 28 s) |
  | merged | 28.7 min (import 821 s, HLODs 776 s) | 6.5 min (import 239 s, HLODs 28 s) |

  The first metro build's 21.6 min had the shaders cached but not the meshes. Cold, the HLOD builder
  (which renders) compiles shaders for ~13 min whatever the merge. Metro stays experimental.
- The import check (`ue.verify_samples`) skips merged pieces and the tile slots.

## Extended building types (2026-11-01)
- `city_extended.py`, behind `CitySpec.extended_types` (Layout tab tick box "Extended building types";
  `POST site/extended`). Off for every project made before it and left out of layout.json while off, so
  every preset's plan, the standard size and the byte-identity tests are untouched; `sw_site.init` (new
  city projects) and `city_roll.roll` turn it on (`roll(extended=False)` for a test that pins a roll).
- Each type is a `Structure` of `Piece`s on the shared unit meshes (box, gable, cylinder, dome and a new
  16-sided `cone`, `blender_greybox.make_mesh`), so Blender and the Unreal stand-in instance them like the
  housing boxes. One greybox slot per type and tile (slot type = type id, `typology` = type id). Placed at
  the end of `city.plan` from its own stream (`city_extended.rng`); it clears buildings, pads, trees and
  stalls under its footprint.
- The viewer's building rows gain a 10th column (index into `shapes`) for non-box pieces; boxes keep 9, so
  a city with the setting off has the same rows as before (`types` gained the new names at the end).
- Building types editor: the catalog GET appends the types' entries (`"extended": true`), the PUT splits
  them off into `CitySpec.extended_looks` (only the ones that differ from `DEFAULTS`). They never join
  `city.typologies`: a non-empty catalog switches the whole fill to `city_types.mass`.
  `city_extended.all_typologies` is what frame prompts (`s0_frames`) and the library look up by id.
- Castle (walled-castle: `inner_wall`; star fortress: a star wall): on the main node, which then gets only a
  paved forecourt (no monument, stoas, rings or civic ring row). A rolled walled-castle's castle node is an
  organic hill node: its summit pad is levelled to the castle's corners (`castle_pad_r`), lanes and streets
  are cut out of that disc (`clip_ways`) and it joins the plan's `cut` (no blocks). The gate faces
  `castle-gate-av` (which now ends at the gatehouse), else the widest road ending at the hub, squared to a
  grid city. Curtain walls go in <= 24 m segments, each on its own ground, so they step down a slope. The
  node's tight shot looks at the castle (it replaced the monument the shot used to look up).
