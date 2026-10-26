# World dressing: progress

Optional world dressing for "Build city in Unreal" / `ap ue build`: ground textures, rocks,
vegetation, water, and Megascans in place of the free defaults. Design: PLAN.md item 6 "World
dressing"; findings: docs/notes/unreal.md "World dressing"; user docs: README "World dressing and
Megascans", USAGE "Unreal Engine 5.8".

## Steps

- [x] Scratch setup: scratch runs pin `AP_UE_PROJECTS` / `AP_UE_BACKUPS` to the scratch copy (the
      shared user config's `projects_dir` wins over the scratch root's local toml).
- [x] Options in project.json (`unreal.dressing`), remembered per project; roles and sources.
- [x] CC0 defaults in the manifest (ambientCG Grass004, Ground106, Rock030, Ground093C; Poly Haven
      rock_07, boulder_01, shrub_02), direct-URL downloads in `ap models` (`--feature dressing`).
- [x] Blender fallbacks for roles without a suitable CC0 asset (umbrella pine, cypress, olive,
      grass tufts; rocks and shrub too when their download is missing).
- [x] Placement plan (`asset_pipeline/dressing.py`): deterministic per seed, keyed cache, rocks off
      buildings / streets / plazas / water / street-ringed blocks / building slots.
- [x] Unreal side (`unreal/ap_dressing.py`) inside the import run: assets, ground material, simple
      sea, ocean (experimental), HISM instances with cull distances, placeholder trees hidden and
      restored, overrides cleared when an option goes.
- [x] Megascans: scan (incl. WSL Windows paths), role guesses, mapping kept across rescans, Fab /
      Bridge plugin detection with the right instructions.
- [x] CLI flags (`--ground`, `--rocks`, `--dress-vegetation`, `--water`, densities, `--megascans`,
      `--no-dressing`), `ap ue scan-assets`; build report lines; UI group with tags, tooltips,
      sliders, Find my assets.
- [x] Tests (tests/test_dressing.py, manifest and download tests); README, USAGE, PLAN, notes.
- [x] Scratch run (2026-10-25, scratch UE copy, second UI on 8701): everything on with the free
      defaults (~80k instances, import ~30 s); rocks unticked → removed; Ocean → falls back to Simple
      sea (the Water plugin crashes the commandlet; never tried headless now); mocked Megascans
      scanned and mapped in Find my assets, a Megascans build falling back per role; two shots
      (ground, aerial) on GPU 0.

## Open
- The ocean needs the full editor (Slate): a step in an offscreen editor run could place it.
- `ue.pick_gpu` can fall over to ComfyUI's GPU without checking ComfyUI (docs/notes/unreal.md).
