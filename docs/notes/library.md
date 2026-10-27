# Library

The asset library: plan, procedural vs TRELLIS, materials, build guidance, file store, versions, attached pieces. Moved from CLAUDE.md on 2026-10-21; rules here still apply when you work in this area.

## Asset library plan (phase 3 step 1, 2026-10-10)
- `ap library plan` (CPU, ~2 s) groups greybox slots into entries (A landmarks and kit
  monuments, B singular civic, C fill typology x material, D props and trees) and finds each
  one's best crop in the locked shot frames through the object-id pass. `ap library check`
  (SAM 3.1, ~2 min for 6 frames, masks cached) flags crops mostly trees, sky or something
  else. Decisions per entry: Library tab › Asset review (`site/library_review.json`).
- **A locked frame can predate the current layout.** Locking keeps the camera, not the
  city: frames locked before a relayout no longer line up with the shot's current id pass,
  so their crops show trees or sky where the old layout had a building. `frame_fit` keeps a
  frame only if its edge match against the current canny is >= 0.9 x the one recorded
  when it was generated, and >= 0.25 (raised scale-model frames never followed theirs).
  Round 2 frames whose view didn't change (match unchanged) still pass.
- Trees and stalls are pieces inside a slot: the id pass can't separate them, so their
  crops are the whole slot (flagged as approximate).
- Crop refinement A/B (2026-10-10, `scripts/diag/crop_refine_ab.py` on the 5 worst crops;
  env `envs/segdepth` from `scripts/install_segdepth.sh`, GPU 0, ~1 s per frame per model
  warm): ID region ∩ ADE20K building-like labels, minus painted-depth occluders (Depth
  Anything V2 fitted affinely to the greybox's 1/Z), then SAM 3.1 inside the box
  (`workflows/sam3_box`, `segment.detect_box`).
  - Where the frame paints the asset, it cleans the crop (sky and trees out): 47-82% of
    the id region kept. Where it doesn't (an asset behind trees, a hillside drawn as a
    terrace wall), only 5-21% is kept: a reliable "no usable crop" signal.
  - OneFormer and Mask2Former (Swin-L ADE20K) agree within a few points; OneFormer's
    weights are MIT, Mask2Former's HF card says "other". Depth Anything V2 Small is
    Apache-2.0 (Base/Large/Giant are CC-BY-NC-4.0).
  - The SAM box step changed one crop of five (+14% on a clean facade).
  - transformers 5.19 reports the Swin backbone's final layernorm as missing for both:
    the heads read the stage outputs, and the labels are right (checked on a frame).

## Library 3D: TRELLIS vs procedural kit (2026-10-10)
- From one front view, TRELLIS guesses depth. A monumental gate came out as separate slabs with
  gaps, and a stoa bay as a four-legged table with no back wall. From a three-quarter view the
  gate's walls are solid, but it came out twice as deep as wide, with the arch on the short end.
- Flux won't draw a three-quarter view of a thin kit module. Eight stoa-bay sheets, in two
  wordings, gave open door frames or walls seen head-on; "side edges cut straight and open"
  reads as a doorway. For whole buildings the three-quarter prompt
  (`ap library sheets --view three-quarter`) works better than the turnaround: a basilica lost
  the roof crosses the turnaround drew, and a market hall showed its depth.
- Multi-storey facade bays (alone or on a kit sheet) come out as whole buildings; a two-column
  bay doesn't.
- So: simple architectural kit (arches, colonnades, walls, cornices) is procedural
  (`asset_pipeline/kit_proc.py`, `scripts/blender_kit.py`, spec in the project's
  `library/kits/<asset>.json`). Real size comes from the greybox, proportions are measured on
  the sheet, and the PBR maps come from the sheet: the delit front elevation projected onto the
  front and back faces, a mirrored seamless tile from a plain patch, and normal and roughness
  from the luminance. About 500 faces, a few seconds on the CPU. TRELLIS stays for complex and
  irregular assets.
- An arched wall's piers must run its full height. The comb of trapezoids only spans the
  opening, so piers cut at the springing line leave the sides open above it.
- Fill typologies (tier C) are one representative building at the catalog's middle sizes (a
  row form as 4 houses). Their slots hold whole blocks: 230 m "townhouses".
- A square 65 m courtyard block at 7 storeys still drew a 2-3-storey villa: the insula needs
  its storeys and the courtyard in the words.
- Procedural buildings (`asset_pipeline/building_proc.py`; `ap library brief` / `ap library build`):
  generators for hall_vault, cube, basilica, podium_tower, perimeter and row. Walls with
  windows, arches and round openings are sliced into convex prisms at every opening edge and
  along every curve. Overlapping walls must not share faces: side walls run between the front
  and back walls, or coplanar end caps show as dark seams.
- Reading a sheet: the palette (k-means) and a plain wall patch work on any view. The patch must
  avoid shadows: score on tone as well as flatness, and pin `patch_px` in the brief when it
  picks up ribs or glazing. The bay and storey rhythm (autocorrelation of the darkness profile;
  an edge profile counts both edges of every window) is only clean on near-frontal views.
  Three-quarter views gave 33-42 "storeys", so rhythms outside 2.5-15 m bays and 2.6-7 m
  storeys are dropped and the generator defaults apply.
- **Every procedural mesh was inside-out until 2026-10-10.** `_quad_prism` wound its faces
  inwards. Cycles shades both sides and hid it; Unreal culls back faces.
  `tests/test_building_proc.py::test_built_meshes_face_outwards` checks the signed volume.
  The Blender prism now orients each polygon counter-clockwise itself.
- Facade grammar (`building_parts.facade`) and per-form defaults (`building_gen.TYPE_FEATURES`).
  A part missing from a build goes into the type defaults, so every building of that form
  gets it; the brief's `features` override them per asset.
- Sheets are drawn at their own scale: a 65 m catalog-middle insula against a sheet whose front
  measures about 37 m at its own storey height made a long low slab. To match a sheet's massing,
  take the front width from storeys x storey height (the measured storey period); keep it inside
  the catalog range.
- Mirror-tiling a small sheet patch that has a gradient gives kaleidoscope patterns. Tiles now
  use the patch's high-pass detail on the storey band's median colour, made seamless by a
  cross-fade with a half-rolled copy and sized to the patch's real extent.
- Frontality (the share of strong edges within 4 degrees of square) picks the near-front view.
  Curved buildings (vaults) score low even on true elevations; pick those by eye.
- **Catalog storey ranges feed the type mix.** Raising library_cube to 11-13 storeys and
  market_hall to 5-6 reshuffled the whole city: footprint x storeys counts towards the type
  shares, and 246 of 485 slots moved. Reverted. `Typology.storey_m` (a per-type storey height)
  changes height only: same 485 slots, same ids, only those types' 18 slots changed. Check with
  an in-memory `city.plan` diff before a rebuild. The podium tower's `podium_storeys` and
  `tower_scale` are height- and width-only in the same way.
- Build review (Library › Asset review › Build review; `library_review.py`): renders per round
  in 3d/library/<asset>/review/round_N (a Workbench preview in about 6 s, Cycles about 12 s
  later), statuses and history in site/library_review.json under "_reviews",
  `ap library review apply`. Deep link: `/?project=<slug>&review=<asset>`.
- Overlapping coplanar faces (a step's top in the plinth's plane) render black in Cycles: offset
  them or don't overlap.

## Material library (the default since 2026-10-11)
- `asset_pipeline/material_lib.py`: type per storey band read off the sheet by the local VLM
  (band crops; ~10-20 s per building warm), colour from `band_colour`. ambientCG 2K sets in
  `$AP_ROOT/materials/ambientcg/` (CC0): Concrete034, Tiles143 (limestone, large dressed blocks,
  2 m; Bricks008 and Travertine009 registered alternatives), Marble019, Plaster001, Bricks086,
  WoodSiding009. `building_proc.DEFAULT_SURFACE = "library"`; `--surface patch` keeps the old
  sheet tiles. Demo:
  `scripts/diag/material_demo.py PROJECT ASSET` (patch vs library in Blender vs Unreal).
- Tint = albedo x (target / scan mean) in linear RGB: baked per band for Blender (the GLB
  carries it: ~20 MB vs ~2 MB with sheet tiles), and in `M_AP_Surface` in Unreal from the
  texture's lowest mip, so any scan (Megascans too) takes the sheet colour.
- Ground storeys (2026-10-11): band crops are upscaled to 1536 px and the prompt says to judge
  the piers between openings. One sheet's brick piers still read as limestone (brick and ashlar
  blur at that scale): set `material_types` in the brief.
- Contrast: the scans are far more speckled than the sheets' drawn walls. The tint also scales
  the scan's spread around the target (linear) to the band patch's relative luminance std
  (factor 0.2-1; a limestone wall came to ~0.4), in Blender and as `Contrast` in M_AP_Surface.
- Trim (slabs, balcony fronts, parapets, copings) is Plaster001 (Concrete034 on concrete
  buildings) in the storey-line colour, never the wall stone. GLBs embed 1K maps (a perimeter
  block: 21 MB -> 5.5 MB); Unreal uses the shared 2K maps.
- Matching the Blender review look in Unreal: manual exposure (EV100 0, bias -0.15), tone curve
  off, sun at W/m2 x 1.2 lux and yaw -50 (Blender's rotation mirrored in Y).
- Megascans come only through Fab with the user's Epic login (some listings are paid): the
  UE preview looks for the type's names among material instances under /Game and falls back
  to the ambientCG maps; the report says which.
- **A Masks sampler with the engine's sRGB WhiteSquareTexture as default fails to compile**
  on SM6, silently: every instance renders as the grey WorldGrid checker and the import still
  reports ok. Use a linear TC_MASKS default; `ue.material_preview` fails on "Failed to compile
  Material" in the log.
- **Coplanar twins rendered black (found 2026-10-11).** Faces of two parts in one plane facing
  the same way: roof slabs sized to the outer walls (the 0.45 m band under every coping),
  attics and accents built over the full-height court facade, recess returns overlapping the
  wall beside them by half a thickness (vertical strips at wing junctions). Every face had a
  material and normals were fine (checked with an emission ID render, a backfacing render, and
  Cycles with normal maps off and flat shading); a raycast put every black pixel on a twin.
  Cycles' shadow ray from one hits the other; Unreal z-fights. `building_parts.resolve_coincident`
  (called by `kit_proc.run_blender`) pulls the losing face back 2 mm (trim over walls over roof
  slabs, later parts win ties) until `coincident_area` finds none: ~5k faces on a perimeter
  block, 0.3 s. Tests: no twins on any form; every GLB face has a material.

## Arcades and build guidance (2026-10-17)
- **The full test suite runs the live ComfyUI tests whenever ComfyUI answers** (they skip only
  when it's down): a plain `pytest` queued a local Flux job on the user's running ComfyUI. Run
  `pytest -m "not comfy"` unless the live parity test is the point (and ComfyUI is yours).
- Guidance on off parts (None): the vision model switches a part on by restating its template's
  values, so an edit equal to the template still counts (`switch_on`). It also needs the mapping
  spelled out: party walls -> pattern `solid` + ground kind `solid` (it proposed `none`); windows
  over an arcade are `arcade.gallery`'s, not `side.window` (it edited the plain back faces).
- Review renders fit every bounding-box corner in the frame (`blender_render_asset.py`): from the
  three-quarter view a tall, deep module's arches fell below a frame fitted to its height only.
- Shell: `cd X && nohup cmd &` puts the subshell's PID in `$!`, not the server's; killing it left
  the old server on the port (the new one failed to bind). Start servers without the `cd &&`,
  and check `ss -ltnp` after a restart.

## Self-serve asset loop (2026-10-16)
- RUNBOOK.md is the artist's guide; areas (`ap area`) and the batch commands' `--area` /
  `--district` are the unit of work. Builds stamp the brief's sha (procedural.json, instance
  index): batches skip unchanged briefs and approved assets, and resume.
- **Brief features merge at every depth** (`building_gen.features`). Until 2026-10-16 a nested brief
  dict (e.g. `top.facade`) replaced the type's whole dict, and the grammar defaults filled in
  the rest: the insula attics got FACADE's 0.9 m parapet and cornice instead of their type's.
- The brief form shows every facade grammar field (`building_gen.form_features`), and saves
  only the difference from those expanded defaults. Keys a generator passes per facade
  (`FACADE_KW`: storeys, heights, parapet, cornice...) are never filled in, or a brief would
  override the generator.
- `kind` means different things per part: enums are keyed "parent.key" first (a cube's portal
  kind is open / glazed / door).
- Vegetation: `zone` parts carry no geometry (kit_proc splits them off before Blender and
  writes `<glb>.zones.json`); planters draw only ground cover. Street-tree slots never touch
  the plan's random stream (CLAUDE.md: the plan reshuffles on any change).
- Blender `bmesh.ops.convex_hull`: geom_interior and geom_unused can list the same vertex;
  dedupe before deleting, or "found the same BMVert used multiple times".
- Review renders fit tall assets: the camera distance covers the height too (a 46 m campanile
  was cropped at the old size * 1.55).

## Library entry duplicates and names (2026-10-18)
- **A duplicate copied its source's `districts` instance counts and `instances`** (shown as "1×",
  and counted in district areas) though it owned no slots. Duplicates now start with no slots,
  instances 0 and no district counts; the source keeps all of its slots.
- Plan names were written once by `asset_plan`: a later rename left Views and 3D (and Reference
  sheets) on the old name. `set_name` now refreshes plans/library.json, and the UI labels every
  library asset from `sw_library.asset_names` (served as `names` with /library).
- Rename carries the id into variant sets and areas/*.md; Unreal assets made under the old id
  (Preview/<id>, LookTest, look-scene builds, pulled actor labels) come back as `not_updated`.
- Duplicates as cards and variants (2026-10-25): a duplicate has no shot frame (`sw_library.load`
  clears the `best` older duplicates copied; a frame-crop decision becomes "description + chosen
  image" with the original's crop) and joins its original's areas. It is a variant of its original
  by default (`variant` on its custom entry): `variants.splits` / `assignments` deal the original's
  prop and kit-slot placements out evenly in hash order, only to variants with a built Active
  version; `city_library.plan` places them (`ap ue build`). Typed buildings keep their instance builds.
- `lrAssetsOf("canopy-table")` matched `canopy-table-02` (prefix): the original's card showed the
  copy's Build review. Modules must belong to the entry (`assetEntry`).
- Delete entry (user-made entries only) moves the files to `<project>/trash/<UTC time>-<id>/` with
  an `entry.json` of what it removed; stored files stay linked there until the trash is emptied.

## Shared file store (2026-10-18)
- `asset_pipeline/filestore.py`: sheets, views and all GLBs under 3d/ live in `files/` by hash
  (0444); entry and version paths are **hard links**, not symlinks: `review.rel` and ~10 other
  places call `.resolve()`, which would turn a symlinked sheet into its store path and break
  star / chosen keys. Readers are unchanged.
- **Any writer that replaces a stored path must `filestore.detach(path)` first** (and `intern` after):
  run_blender, s5_3d.run, s3_views.cut, s2_refs.generate do. A missed one gets PermissionError on
  the read-only inode instead of silently changing another entry's file. Copies go through
  `filestore.copy` (links stored kinds).
- A shared inode shares its mtime: "newest TRELLIS result" now comes from the result's own stats
  JSON (`s5_3d.result_time`), and restore touches that JSON, not the GLB.
- Deleted sheet numbers go into meta.json `deleted` and are never reused (a brief or version
  naming the old file must not come to mean a new sheet).
- Migration 2026-10-18 on a city project: 971 files -> 917 objects, 272.8 MB freed (duplicates and review-round copies).

## Version in use (2026-10-18)
- **Restoring a TRELLIS version never made it current** when the asset had a brief: `current()`
  always preferred a procedural version, and restore only touched the result's mtime. The job
  reported success, so nothing looked wrong. Now `versions/current.json` holds the version the
  pipeline uses: set by "Use this version" (which also switches the entry's route to its kind and
  raises if the switch didn't take) and by a new build whose kind matches the entry's route.
  Without a pointer the old rule applies.
- `versions.current_glb` is what cleanup (and so export) takes; `versions.cards` is Views and
  3D's model cards (one per version with a model, the one in use first). A procedural version owns
  the build on disk when procedural.json's brief_sha matches its own: switching back to it needs no rebuild.
- TRELLIS outputs no version lists are adopted as versions (never made current); other loose
  GLBs are listed under Tools › Storage….

## Attached pieces (2026-10-18)
- `asset_pipeline/assembly.py`: decisions[parent]["children"] = [{entry, anchor (centre/front),
  offset, yaw, scale}], one level. Frame: the parent model's footprint centred, base at 0, metres,
  Z up, front -Y; anchors are read off the parent's current model each time (rebuilds at a new
  size keep children on the front edge). A TRELLIS child is fitted to its entry's height.
- GLB bounds come from the glTF JSON (accessor min/max + node transforms), no Blender: cheap
  enough for the UI and look placements.
- Review renders and the Asset review preview use `3d/library/<parent>/assembly.glb`
  (scripts/blender_assembly.py; placeholder boxes for children without a model), rebuilt when
  its stamp (model paths, sizes, mtimes, matrices) changes.
- Unreal: look scenes add child placements (full matrix with scale, `parent` key); ap_look_test
  attaches them with KEEP_WORLD and shares an outliner folder. Verified in UE 5.8 with a
  commandlet probe (attach, world location kept, scale, folder). Children never clear greybox.

## Sheets and saved descriptions (2026-10-18)
- **A description saved in Manage entry… never reached the sheets** (forum-statue drew the old
  placeholder text): sheets were prompted from plans/library.json, which `sw_library.describe`
  didn't rewrite. Now describe/set_name call `sw_library.refresh_plan`, and `s2_refs.generate`
  re-reads the entry's fields itself (`s2_refs.current`). Each sheet records its `description`;
  older ones are recovered from their prompt (`described_from_prompt`, style text after the template).

## Reroll and shading (2026-10-22)
- **Procedural Reroll gave identical versions** because the build only changes when the brief does:
  guidance with no brief field (smooth shading) produced no edits, and New seed only reaches TRELLIS.
  Now each rerolled version says what changed in the brief and what had "No setting for", and a
  build byte-identical to an existing version (file store hash) adds no version. A reroll without
  guidance re-measures the sheet and can move dims (a hall's height went 19.5 -> 13.71 m).
- **"What changed" listed dozens of "auto -> value" settings** (2026-10-23): not written by the Unreal
  build (the briefs held only explicit values); the comparison ran on `brief_guide.values()`, which
  expands a part switched on (attic) to its whole template. `brief_changes` now compares explicit
  paths only (resolved values are just the "before"), a part on/off is one item, labels are plain
  words. The card shows `changes_short` (one line, "+N more"); the full list is the tooltip; every
  card line is single-line so cards keep one height. Old stored "Brief: path a -> b" lines are
  converted on read (`stored_lines`).
- **Curved procedural surfaces shaded as facets whatever the smoothing angle**: parts that touch face
  to face (the 18 prisms of a barrel vault) left their shared faces inside the mesh, and every seam
  met one at 90 deg, so smooth-by-angle kept all 932 vault vertices split. `blender_kit.py` now
  deletes those twin faces before the weld (`remove_doubles` keeps one of each pair, which hides
  them from a check after it); afterwards the vault had no split seams under 30 deg, only its real
  corners. Brief `shading` (auto | smooth | flat): auto smooths curved forms at 30 deg.
- **"Guidance does nothing" (2026-10-22, later):** an ap ui still running the code from before
  the reroll changes (no change line, no duplicate check) while the Blender script, read fresh
  each build, already had the new face handling. The guidance had reached the brief: "concrete
  outside" set materials the sheet reading already chose (concrete in every band), and
  "brutalist" added a planter style to a form with no planters, so both builds were identical.
  Now the vision model only sees the fields the builder uses (`building_proc.steerable`: each
  setting nudged and the parts regenerated, no Blender), duplicates are matched on
  `kit_proc.build_sha` (the Blender job with textures by content, plus the script) as well as the
  GLB hash, and guidance with no matching setting ends the job without a version. Run without the
  "never stand in for something with no field" rule, the local VLM answered "a gold dome with a
  flag" by removing the hall's ridge cap.

## Reroll materials and the reroll audit (2026-10-23)
- 3D previews were never untextured: the GLBs embed each slot's baked texture. They looked white because
  (a) model-viewer's default (ACES, exposure 1) burnt pale surfaces out (now `MV_LOOK`: neutral tone
  mapping, exposure 0.75, close to the Cycles thumbnails), and (b) every band was tinted to the sheet's
  pale colour at the sheet's low contrast, so brick → concrete barely changed the look.
- `building_proc.named_bands`: a band whose `material_types` differs from the sheet's reading
  (measured.materials) takes the scan's own colour and full detail (or the brief's `colors[band]`),
  on library and on sheet-patch surfaces. Unreal reads the same colour/contrast from the job, so it follows.
  Existing briefs with such an override (e.g. an explicit base brick) look different on their next build.
- `travertine` is a library type (Travertine009, already the limestone alternative); `TYPES` (what the VLM
  reads off sheets) is unchanged. Material words without a scan (granite, sandstone, slate…) go into the
  Changed line: "no surface-library texture for …".
- Audit (scratch, one reroll per builder, geometry + material guidance): all 7 builders on Views and 3D
  reach both. The one failure was arcade: "taller arches" set arch_h past the storey (crown 0.3 m under h)
  and validation dropped it. `brief_guide._companions` now moves the coupled arcade field with the edit.
- Geometry checks: compare `building_proc.generate` parts from the two briefs; GLB vertex data differs with
  material tile sizes (UVs) and with code changes since the old build.
- A scratch AP_ROOT's VLM needs its own ports (`[local.models.*] url` in its backends.local.toml), or it
  takes the main root's 8711 and that app can't start its own.

## Kit assembly (2026-10-25)
- `asset_pipeline/kit_assembly.py`: a block slot assigned to a facade kit (an entry decided "kit",
  or standalone entries with a role grouped by kit id) is built from its modules in the city
  manifest. Roles: bay, corner, end, filler; stored per asset as `kit_role` in
  site/library_review.json (Manage entry… › Kit modules), else detected from the id, the brief
  (arcade on two adjacent faces = corner) and the piece description.
- Footprint: union of the slot's greybox masses (>= 2 m high, >= 4 m2; mesh bounds from the
  export index), one assembly per block. Corners at outer corners facing out (front -Y, left -X),
  bays scaled along x by at most 10% per edge, then fillers, else a gap in the export log and
  `library.kit_notes`. Inner corners take end modules or the runs meet.
- Heights (vertical.py, 2026-10-25): each module, and each single-building slot's build, is
  rebuilt at the height of the masses under it (height variants in 3d/library/<asset>/heights/,
  cached per role, storey count and parts; ~3 s each in Blender). Layers stack only past a
  builder's MAX_STOREYS (row 6, perimeter 12, arcade 16, podium_tower 60) or for modules with
  no brief (copies, reported). Layers get roles bottom / middle / top (no ground floor above the
  first, no roof features under another); facades without a ground storey use the ground kind
  "upper". A setback mass on a larger one is its own tier (bottom/top roles); a greybox ground
  storey under its upper storeys (same footprint) stays one tier. The placement check adds
  `ground_floor_up` and `roof_under`. On the scratch city (Blender, no Unreal): 857 / 1219 of
  those before, 0 / 7 after (the 7: overlapping corners on short upper-tier edges); 109 variants.
- Placement fixes (2026-10-25): (1) a setback mass is clamped to what it stands on (gaps under
  6 m closed: an arcade's walk) and is a setback by the part it keeps; an upper tier whose ring
  won't stay on the tier under it (16 m corners on a small top) folds into that tier
  (`fold_tiers`: the lower modules under it rise to its top); blocks under 1.5 m mean thickness
  (walls left between masses) get no ring. Check kind `overhang`. (2) Row builder
  `min_house_w` (default 5 m, `building_gen.row_houses`): fewer houses, one at the whole width
  when even one is narrower; instance index records `houses`, so `skip_fresh` rebuilds stale
  rows. (3) An entry whose Active version isn't procedural places that model at every one of its
  placements (`city_library.use_active_models`, one mesh per entry and version, scaled to each
  footprint and height; its own baked maps, never the entry's procedural materials) and the build
  report says so. Scratch test: three entries given the real project's TRELLIS Active versions.
- All modules approved, or the slot stays greybox. Written 2026-10-25 in a cloud session on
  synthetic footprints only: NOT yet run in Unreal.

## Batch decisions in Asset review (2026-10-25)
- Over the list: Description only / Exclude for all shown (the Show filter's cards not already so; cards whose
  Frame crop is available are skipped unless "include ones with a frame crop" is ticked), and a tick box per card
  for Frame crop / Description only / Exclude / Clear on just those. Both confirm with the list of assets.
- `POST /library/decisions` {entries, decision}: skips kit entries, vegetation and Frame crop without a crop, then
  `library_entries.sync_plan` (new decisions become sheet units, excluded leave), so Generate missing updates.
  `sync_plan` now also rewrites the plan when its last included entry leaves.
- Test: tests/test_library_batch_decide.py (API and browser).

## TRELLIS shading (2026-10-25)
- A TRELLIS GLB carries continuous per-vertex normals (creases at 0.01% of positions on the rotunda
  v2 test) but its vertices are split along UV seams (1.29x). Unreal shaded it faceted because
  Interchange built it with `recompute_normals` on and glTF has no smoothing groups.
- Fix: every TRELLIS result (and Clean up) is welded and shaded smooth by angle in Blender
  (`smooth.py`, `scripts/blender_fix_glb.py`, `[trellis] smooth_angle` 30 like the procedural
  builds), and Unreal builds every imported mesh with the GLB's normals (`ap_import.keep_normals`;
  existing TRELLIS models in CityLibrary switch on the next build). A ~1M-face model takes ~3 s.
- Existing versions: `ap library smooth PROJECT ASSET | --all` (UI: Smooth shading on a TRELLIS
  version row in Views and 3D's Versions box, Pipeline › Smooth TRELLIS models…) writes `<stem>_smooth.glb` as a new version that
  takes over as Active when its source was; the original stays.
- The 30 deg pass leaves real creases on ~18% of a TRELLIS mesh's positions (its noisy surface plus
  cornices and steps); in clay renders the dome's facet bands go, what remains is the remesh's own
  geometry (a slightly polygonal silhouette), which normals can't change.

## TRELLIS ground slabs (2026-10-26)
- TRELLIS often builds the sheet's cast shadow or floor into a thin plate under the model (an arch
  test: a plate reaching 1.3x the arch's footprint beyond it). `scripts/blender_ground.py` finds it on a plan
  grid: footprint = geometry between `ground_max_thickness` (5% of height) and 30% of height, taken
  as its convex hull (courtyards, pools, the passage between piers count as inside); a slab = the
  low layer reaching more than `ground_min_overhang` (6% of the footprint's size) beyond that hull
  over at least `ground_min_area` (4% of its area). The cut is at the slab top (90th percentile of
  the overhang's heights) plus a margin; bisect keeps UVs, stray bits of plate go, the cut under
  piers is closed, the model is re-seated at z=0. ~0.6 s detection on a 1M-face model, ~15 s with
  Blender's import/export.
- Sweep of 23 TRELLIS models from a real project: 3 slabs (a plate, a tiled base, a pavement apron);
  kept: stepped and plain podiums, a statue plinth, a courtyard pool, a tent's floor inside its
  walls. The first version (bare footprint, no hull) also flagged the pool and a
  stall's deck: the hull fixed those. Sampling triangles by 3D area matters (coarse walls).
- Runs on every TRELLIS result after smoothing (`[trellis] remove_ground`) and in Clean up (before
  the fit). Existing versions: `ap library remove-ground PROJECT ASSET|--all [--smooth]`, the version
  row's Remove ground, Pipeline › Remove ground from TRELLIS models… (a tick smooths in the same
  Blender pass; one new version per asset). No ground found: no version, the source's stats JSON
  records it (its mtime kept: `result_time` reads it) and the card says so.
- Prevention: stage 3 mattes the floor and shadow under SAM-found views (`s3_views.matte_ground`,
  `[views] matte_ground`): below the lowest quarter of the mask, outside the mask grown 3%, only
  grey and smooth pixels (shadows) and their rims. A bare "outside the mask" matte ate plinths SAM
  missed; a view whose mask covers < 90% of its band's content is left alone. A floor strip SAM
  includes in the mask stays, so the mesh pass still matters.


## Unreal places the Active model's Clean up output (2026-10-26)
- `ap ue build` placed an entry's Active TRELLIS model from its raw version GLB (~1M faces) even
  when Clean up had made a decimated, baked one. `city_library.active_model` now takes the newest
  Clean up output whose `source` is the Active version's GLB and whose `usage` is the asset's tier
  (`s6_cleanup.for_source`), else the raw GLB; the mesh is `active_<entry>_v<n>_<usage>` and the
  build report's line per entry says which ("Cleaned (game, decimate) 30,000 faces" / "raw GLB").
- A Clean up of an older version doesn't count: a new version (smoothed, ground removed) needs its
  own Clean up. Views and 3D labels each Clean up card "Cleaned from vN" (Active marked) and hides
  other versions' behind Show older cleanups (remembered per asset until reload).

## Cleanup steps per version; Keep removed (2026-10-27)
- Clean up runs per version from its row in Views and 3D (`POST /cleanup` with that version's
  `glb`); `s6_cleanup.run` records it on the version (`cleaned: {usage: output}` in versions.json,
  `versions.note_cleaned`). `versions.cleaned` also infers it for older cleanups from the report's
  `source` (`s6_cleanup.for_source`). Smoothed / ground looked for already come from the GLB's stats
  JSON and file name (`smooth.is_smoothed`, `ground_checked`); a fix version copies the source's stats,
  so it carries the steps done before.
- Keep (pin) is gone: retention (`prune`, `removable`) always protects the Active and Approved
  versions, and versions pinned earlier stay protected (and don't count towards the last N).

## Cleanup details per version (2026-10-27)
- Clean up's report now also records what the run asked for: `fit`, `budget` (the game triangle
  target, None for cine/hero), `bake_size` (game bakes) and `textures` (the largest texture in the
  output GLB, [w, h]). Blender's report already had `input_faces`, `output_faces`,
  `output_dims_m`, `base_z` (pivot at the base centre) and `retopo_method`.
- `s6_cleanup.details` returns only recorded values; for older reports the texture size is read
  from the output GLB's image headers (`texture_sizes`, cached per file and mtime). The version
  listing carries it as `cleanup`; Views and 3D shows it under a collapsed "Cleanup details".
