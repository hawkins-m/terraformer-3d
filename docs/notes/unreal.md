# Unreal

Unreal Engine 5.8.3 delivery: headless runs, axes, import, renders, look scenes. Moved from CLAUDE.md on 2026-10-21; rules here still apply when you work in this area.

## Unreal Engine 5.8.3 (world mode delivery) findings (2026-10-06)
- Editor: `/mnt/storage/UnrealEngine/5.8.3/Engine/Binaries/Linux/UnrealEditor(-Cmd)`
  (config `[unreal] editor_cmd`). Python 3.11.8 inside.
- UE projects live in `~/Projects/Unreal/<slug>/` (`[unreal] projects_dir`, the system
  NVMe). Backups go to `/mnt/storage/backups/unreal/<slug>/<timestamp>/`
  (`[unreal] backup_dir`, the other drive): `ap ue backup`, rsync with `--link-dest`,
  10 kept.
- **rsync needs `--checksum` for backups.** Its default size + mtime check skipped a file
  re-saved within the same second at the same size, and the new snapshot hard-linked the
  old content.
- Headless runs: `UnrealEditor-Cmd <uproject> -run=pythonscript -script="<file> <args.json>"
  -unattended -nullrhi`. No GPU is used, and an import takes about 1 min fresh and 10 s
  unchanged.
  - The commandlet exits 1 whenever anything logged an error, so judge by the script's
    JSON report.
  - `EditorAssetLibrary.load_asset` can't load engine content in the commandlet (the
    asset registry isn't scanned); `unreal.load_asset` can.
- **Axes, verified live:** Interchange imports a Blender glTF as Blender +X → UE +X,
  +Y → −Y, +Z → +Z, metres → cm. So every placement converts by C·M·C, C = diag(1,−1,1),
  with translation ×100 (`asset_pipeline/ue_coords.py`).
  - `unreal.Rotator(roll, pitch, yaw)` is positional.
  - The import self-check recomputes 155 instance world positions from the greybox
    independently: all within 1 cm.
- Interchange puts a mesh at `<dest>/<file>/StaticMeshes/<file>`, so the importer moves
  it to `/Game/AP/Library/<id>/SM_<id>`. A changed GLB replaces the mesh with
  `consolidate_assets`, which keeps references. In the commandlet the old asset stays at
  the target path after consolidating, so the importer deletes it before the rename, and
  the rename leaves redirectors in `_import` that are deleted directly (UE 5.8's Python
  AssetTools has no `fixup_referencers`). Verified 2026-10-06 on a full relayout.
- The commandlet deletes the importer's redirectors in memory only; their files stayed in
  `Content/AP/_import` and the next import of a changed GLB there failed ("already an asset of
  class ObjectRedirector"). `ue.import_` removes that folder first (editor closed).
- Stale slots (left where an earlier greybox put them) vs hand moves: `ap ue stale`, and
  `ap ue import --reset-layout --only-stale` resets only the stale ones (2026-10-16: 175 stale,
  0 hand-moved, all 489 back in place). It compares against every committed greybox.blend.
- A wholesale relayout needs `ap ue import --reset-layout --prune` (after `ap ue backup`):
  otherwise actors whose ids the new layout reuses keep their old UE transform (UE is the
  layout source of truth) and the old layout's actors stay.
- InstancedStaticMeshComponents added through `SubobjectDataSubsystem.add_new_subobject`
  persist across save and reload, with label, tags, outliner folder and instances.
- **No Python API creates a Landscape from a heightmap.** Only
  `landscape_import_heightmap_from_render_target` exists, into an existing landscape's
  components (and it needs an RHI). A heightmap project's terrain is a Nanite mesh plus a manual
  Landscape-mode import. A generated terrain gets a Landscape through the repo's editor plugin
  `unreal/plugins/APLandscape` (`ALandscape::Import`, as Landscape mode does): `RunUAT.sh
  BuildPlugin` builds it in ~30 s on 5.8.3 (the uplugin must not list Landscape as a plugin: it's an
  engine module). `-nullrhi` commandlets create and save a 2017 x 2017 Landscape fine.
- **Renaming a project's level (2026-10-22, project slug rename).** A map rename through AssetTools
  (`rename_assets` / `rename_asset`) stops at a modal "config INI may need Find/Replace" dialog that
  `-unattended` answers Cancel, so nothing is renamed. What worked headless: `duplicate_asset` the map
  (every actor, tag, transform and ISM instance identical), drop `LS_<Name>` and let
  `ap_import.sequence()` create it fresh in the new level (a renamed sequence keeps the old map path
  in its name table), then delete the old map file with the editor closed (`delete_asset` in the
  commandlet only removes it in memory). The level has no LevelSequenceActor; only the sequence
  refers to the map. Imported meshes keep their old GLB source path in their import data; that only
  affects the editor's Reimport, since `ap ue import` matches meshes by the `ap:glb_sha` tag.
- Shot renders need the full editor: `UnrealEditor -RenderOffscreen -graphicsadapter=0`
  (Vulkan adapter 0 = GPU 0) with a Slate tick callback. Screenshots taken while
  ShaderCompileWorkers still run show grey default materials, so wait for them to go
  idle.

## Frame reconcile and look scenes (2026-10-11)
- `CitySpec.sites` carve fixed civic ensembles out of the fill *after* it is planned (own random
  stream): the full plan diff showed only the 8 buildings on the footprint gone and the site's 2
  added; trees, blocks, ways identical. A slot that loses buildings keeps its pieces' world
  positions but its pivot recentres (UE keeps actor transforms: re-import with --reset-layout).
- Plaza `basins`: two 12 x 30 m basins either side of a 10 m walk along the node's second radial,
  lawns with 1.8 m gravel edges in two opposite quarters. Greybox pieces without a colour of their
  own take the slot type's colour: a "pool" slot painted its paving blue until `paving` got one.
- `frame_plan` (OneFormer + SAM 3.1 classes read back onto the plan through the shot camera):
  inverse warp, not splatting (far rows leave streaks); at eye level (1.5 m) one pixel row spans
  ~1 m of ground at 40 m and ~5 m at 80 m, so compare within ~90 m. Depth Anything agreed with the
  plane to 1% median. The top-down greybox plan renders its class colours as linear: compare in sRGB.
- The library plan labelled a shot's first usable source frame "locked" when the real lock no
  longer fit the layout (fixed: `locked` is the shot's own lock).
- `library_instances`: an entry built at each typed building's own footprint and storeys (138
  insula instances, ~5 MB each with 1K maps; ~10 s per build).
- **UE offscreen renders of heavy look scenes (~120 Nanite library builds) crash GPU 0 with
  VK_ERROR_DEVICE_LOST** (twice, the desktop GPU); the same level renders fine on GPU 1
  (`-graphicsadapter=1`, `ue._render(adapter=1)`) while ComfyUI is down.
- Interchange imports every GLB's embedded textures even when the pipeline assigns its own
  material instances: ~120 instance GLBs made the import slow. Strip textures for UE imports.
- A 12 deg sun through SkyAtmosphere plus a 4300 K sun and warm fog turned the whole look sepia;
  16 deg at 5000 K with blue-grey haze was closer. Exposure set on the paving converges in one
  correction to ~10% (the tone curve isn't linear). Height fog at 0.025 hides a 3.5-6.5 km ridge.

## Look scenes from presets (2026-10-14)
- `ap ue look PROJECT NAME` (`asset_pipeline/look.py`).
- Exposure on a frame region converges slowly through the tone curve: 0.69 -> 0.55 -> 0.47
  against 0.43 (bias 0, -0.67, -1.02). Use three passes.
- A frame's class median gives a bad albedo:
  - Shadowed pixels carry the sky's blue, so the paving's median was blue-grey. Take the
    hue from the brightest 40% and scale it to an albedo.
  - Water's brightest pixels are reflections: use all of them.
  - The grass and gravel classes picked up warm soil, so fixed tints went into the preset.
- Height fog: 0.025 hid a 2.5-4 km ridge, 0.008 turned it into a pale wall, and 0.0025
  kept it visible and hazy (falloff 0.06).
- A crest-line backdrop needs a front slope steeper than the line of sight, or a nearer
  row becomes the silhouette. Rows at near-1800 (below ground), -1200 (0.3 z), -600
  (0.7 z) and near (z) keep the crest on top.
- World-aligned tiling textures need pixel-centre sampling when generated, or the joint at
  the wrap is half a pixel off.
- **Attaching a library actor to its slot actor (2026-10-22).** In the commandlet an
  `attach_to_actor(..., KEEP_RELATIVE)` followed by `set_actor_relative_transform` did not
  persist: after the save the actors had no parent and the relative offset stood as their world
  position (hundreds of metres from their slots, below ground, slot yaw missing). Set the world
  transform first (the slot actor's current transform x the relative one), then attach with
  KEEP_WORLD; the position is right whether or not the attachment holds.

## World dressing (2026-10-25)
- **A scratch AP_ROOT resolved to the real Unreal project**: the user config's `[unreal]
  projects_dir` wins over the scratch root's `backends.local.toml`. Fixed 2026-10-25: a project sets
  its own `unreal.project_dir` / `backup_dir` in project.json (they win over config and env), and
  `ue.check_target` (in `ue.init`, `ue.backup`, `ap ue build`) refuses a project from another storage
  root that would fall through to the user config's folders, and an Unreal folder whose
  `.terraformer-project` names another pipeline project (an unclaimed folder is claimed).
- The dressing runs inside the import's commandlet (`ap_import.py` → `ap_dressing.apply`), so it
  costs no extra editor start: the first full run with everything on took 37 s of import, later
  ones ~30 s (instances unchanged, materials re-applied).
- **`-nullrhi` doesn't compile shaders**, so a material that fails to compile only shows at render:
  the ground material fell back to the default because its roughness samplers (Masks) defaulted to
  the sRGB `WhiteSquareTexture` ("Sampler type is Masks, should be Color"). Defaults must match the
  sampler (a generated linear white map); check `ap_shot_render.log` for "Failed to compile".
- Interchange imports every mesh node of a glTF / GLB as its own static mesh (Poly Haven sets lay
  several variants side by side); the dressing uses each as a variant and recentres it on its
  bounds, base down, so the file's layout doesn't matter.
- Paving (2026-10-28, `dressing.paving`, `ap_dressing.paving`): M_AP_Paving and an instance per kind
  (Slabs: avenues, streets, ambientCG PavingStones054 at 1.9 x 3.8 m; Cobbles: lanes, stairs,
  PavingStones119 at 1.15 m; Flagstones: plazas and the forum radials, PavingStones128 at 3.5 m) on
  the way and plaza slots' ISMs by name (`ISM_<asset>`; pools, trees and lawns keep their look).
  Near/far: the maps at their size near the camera, at 1/3.7 past FarM (160 m), plus the ground's
  macro noise for brightness, a warm-to-cool tint and roughness. M_AP_LandscapeAuto (MI_AP_TerrainAuto)
  has the same since TERRAIN_MAT_VERSION 2; a version bump re-runs the terrain step on the next build.
  Not checked in a render yet: `-nullrhi` doesn't compile shaders (below).
- Macro noise built from summed sines (integer wave numbers, for tiling) drew parallel stripes on
  the terrain seen from the air; FFT-shaped white noise (1/f^beta, periodic by construction) doesn't.
- HISM per variant with cull distances (grass 40-60 m, shrubs 150-200 m, small rocks 300-400 m) and
  no collision: ~80k instances (55k of them grass) rendered offscreen on GPU 0 at 1920x1080 in ~45 s
  including the shader wait.
- **The Water plugin's ocean can't be placed headless in UE 5.8**: spawning `WaterBodyOcean` in the
  commandlet makes the WaterEditor module set up a landscape brush (`AWaterLandscapeBrush::
  SetTargetLandscape`), which asserts `CurrentBaseApplication.IsValid()` (no Slate) and kills the
  editor with SIGSEGV; Python can't catch it. The dressing never tries headless: Ocean enables the
  plugin in the .uproject (it loads fine) and falls back to the simple sea, saying so in the report.
- `ue.pick_gpu` used to fall over to GPU 1 when GPU 0 held more than 4 GB, without asking about
  ComfyUI (2026-10-25: a preview render landed next to an idle ComfyUI, undisturbed). It now skips
  ComfyUI's GPU while ComfyUI has a job running or queued (its `/queue` through ComfyClient), any
  GPU with a TRELLIS lock or the vision model answering, and one over the VRAM limit; otherwise it
  waits, then refuses with the reasons.
- **Mesh build settings headless (2026-10-25)**: Interchange builds glTF meshes with
  `recompute_normals` on (faceted TRELLIS models). `StaticMeshEditorSubsystem` doesn't exist in the
  commandlet until `Module Load StaticMeshEditor` (console command), and the old
  `EditorStaticMeshLibrary.set_lod_build_settings` silently does nothing without it.
  `ap_import.mesh_editor()` loads it; `keep_normals` builds with the GLB's normals (Nanite too).

## What the next city build places, per asset (2026-10-27)
- `asset_pipeline/next_build.py` is the one source of truth: `gate` (Approved, or any built entry
  with Use Active versions), `building_model` (a non-procedural Active version at building places:
  its Clean up output for the tier, else raw), `prop_model` (props: the Active version's Clean
  up output for the tier, else raw; since 2026-10-27, before that props placed the raw model),
  `piece_model` (attached pieces, assembly.layout: the same; mesh `child_<entry>_<tier>`, and a
  cleaned piece keeps its own baked maps, so its procedural job isn't registered),
  `placed_glb` (the raw Active model: a parent's own frame and footprint) and `procedural_build` (the build on disk and the
  version it is). `city_library.ok_entries` / `active_model` and `assembly.model` call these, so the
  explanation can't drift from the build; `tests/test_next_build.py` compares it with what
  `prop_placements` / `use_active_models` place.
- `explain(store, use_active)` per entry: role (prop / building / none), placed, version, why,
  approved (+ the version approved), cleanup for the tier (`cleaned`, `cleanup_used`), stand-in
  reason and a plain `line`. API: `GET /api/projects/{slug}/next-build?use_active=` (adds the last
  build's placement-check issues per entry from the build report's `per_entry`).
- Roles are read from the greybox (prop pieces, variants), instance builds and entries' slots; an
  entry used nowhere says "not in the city layout". "Not cleaned up" is never a stand-in reason:
  the build places the raw model then (unchanged behaviour); it counts as "need cleanup".
