# Ui

The browser UI: static versioning, script scope, list layout, polling redraws, job progress, job lanes and VRAM. Moved from CLAUDE.md on 2026-10-21; rules here still apply when you work in this area.

## UI basics (from the library 3D notes, 2026-10-17)
- **The UI's static files are versioned (2026-10-17).** index.html is served no-store with
  `?v=<content hash>` on every /static/ URL; unversioned static is `no-cache`. Before that, a
  browser kept the pre-rename shell.js across server restarts and hard refreshes. Python
  changes still need an `ap ui` restart (it prints the code path, git commit and build id);
  static changes show on reload, and an open page shows a Reload bar (`/api/version`).
  Headless UI checks: Playwright with the system Chrome (`channel="chrome"`), its own venv
  `$AP_ROOT/envs/browser-test`.
- UI scripts share one global scope: a second top-level `const clone` silently broke a whole
  script. Check with `cat asset_pipeline/ui/static/*.js > all.js && node --check all.js`.

## Library / Generate 3D Model & Review layout (2026-10-18)
- Only the lists scroll (`.scroll-list`: #assets-list, #refs-list, #review-list; #asset-detail):
  #library-tab / #review-tab are flex columns with overflow hidden. jump.js keeps a scroll position
  per list and project (localStorage `ap.scroll.<slug>`), restored after every re-render and on
  tab / sub-tab switches: a hidden (display: none) list loses its scrollTop, and a re-render's
  momentary empty list clamps it to 0.
- jump.js wraps renderLibrary / renderRefs / renderReview / showSub / showTab by reassigning the
  globals; the tab bars call them late-bound (`s => showSub(s)`), or clicks would bypass the wrapper.
- Show filter: per-project choice (`ap.libFilter.<slug>`), default the current area (last picked
  in Library › Areas…, `ap.area.<slug>`, else the first). /library carries `areas` {name: entries}.
- tests/test_ui_layout.py drives a real `ap ui` on a scratch AP_ROOT with the browser-test env
  (skipped without it). A district-only area no longer needs the greybox (areas.members).

## Polling redraws (2026-10-18)
- While jobs run, `load()` re-renders every ~2 s. Generate 3D Model & Review, Asset review and Reference sheets
  now redraw through `keyed.js`: rows keyed by asset with a data signature; unchanged rows keep
  their DOM, changed rows take over the old row's img / model-viewer elements (a file whose `?v=`
  changed is swapped in once loaded). Put everything a row shows into its `sig`, or it goes stale.
- Two CSS rules shared `.asset-review`: the Views row (grid) and the build-review buttons on
  Asset review cards (flex-wrap, later in the file, so it won). The Views row wrapped its parts
  freely and the version strip jumped beside / below the cards. The card div is `.asset-builds`.
- The version strip caches its data per asset (VER_CACHE) and redraws only on a change.

## Job progress (2026-10-18)
- The Make 3D bars "jumped back" every poll: TRELLIS had no counted progress, so both bars were
  marquees, and the Jobs panel (replaceChildren) and the dialog (innerHTML) rebuilt them on every
  redraw, restarting the animation. Bars are now built once per job and updated in place
  (`paintBar`), widths ease between polls, and `jobProgress` (one source for both) never lets a
  stage or a fraction go back (PROG_TRACK).
- `asset_pipeline/trellis_progress.py` reads the stages from trellis.log: loading (2 passes),
  sparse structure, shape (2 passes on cascade modes), texture are counted (passes folded into
  one fraction); decode, UV/bake and export have no usable count and show as striped stages.

## Job lanes and VRAM (2026-10-18)
- `jobs.JobQueue`: one thread per job, FIFO slots per lane (gpu0 1, comfy 1, cpu `[jobs] cpu_slots`,
  service 1). Jobs with the same `jobs.exclusive` key (a project's site/greybox, its UE project, one
  asset) never overlap, even on the CPU: CPU jobs used to run one at a time and some share files.
- `jobs.hop(lane)` moves a running job (Brief & build: brief on gpu0, then Blender on cpu);
  `jobs.using("gpu0", "vlm")` wraps every local VLM request (`vlm.AutoLocal`), so CPU jobs that read
  band materials or draft slots never run the VLM beside TRELLIS. Brief & build reads the band
  materials (`building_proc.read_band_materials`) before it hops, or the build reloads the model.
- `gpu_policy.GpuPolicy` keeps a GPU job queued until free VRAM >= `[jobs.vram_gb]` (the VLM: its
  model's `vram_gb`); memory of the process the job uses counts as free (ComfyUI's, the loaded VLM's).
  TRELLIS unloads the VLM first; the VLM is unloaded after a VLM job unless another one waits.
- **amd-smi's per-process `vram_mem` is wrong here**: 368 KB for a ComfyUI holding 2.96 GB on GPU 1
  (amd-smi 26.2.2). `gpu.vram_by_process` uses `rocm-smi --showpids --json` (VRAM per PID) plus
  the text of `--showpidgpus` (its devices; that flag has no JSON).
- Tests that need the old serial CPU lane pass `JobQueue(slots={"cpu": 1})`; `JobQueue()` has no policy
  (no VRAM checks), the app builds it with one.

## Workflow header, stages and guide (UX pass, 2026-10-20)
- `asset_pipeline/ui/static/workflow.js` holds the steps (tab order; Site and City are one step) and
  computes every status from loaded data. The header strip, the Stages list and the guide all read
  it. Never mark a step Complete because one file exists.
- Library "Out of date" means a locked frame the plan never considered (`plan.candidates`), not one
  missing from `plan.sources`: `frame_fit` drops old-layout frames on purpose.
- `showSub` hides every `[data-sub]` element in the Library tab and `showTab` every `.tabpanel[data-tab]`.
  Generated buttons use `data-go-tab` / `data-go-sub`; an empty-state button with `data-sub=""`
  vanished as soon as a sub-tab was shown.
- Completion (2026-10-27): `workflow()` downgrades a Complete step whose Next is closed to Needs input
  (with Next's reason), so a label and its Next can't disagree. Library and Generate 3D Model & Review read the
  Library strip's counts (`libWaiting` from `libStepOf`): References ready = nobody waits at Decide,
  Sheets or Star; Views complete = nobody waits anywhere (every included asset approved), "Awaiting
  approval (N)" while N wait at Approve. Unreal reads the Unreal panel's own `ueSteps` (moved from
  unreal.js into workflow.js): Exported, Scene assembled (import done after the export), Render
  verified (renders after the import and a look run) = Complete.
- `tests/test_ui_workflow.py` runs workflow.js in node against stubbed project data. A new global
  that workflow.js touches needs a stub in its harness.
- Test UI changes on a second instance (`ap ui --port 8701` from the checkout); the user's `ap ui`
  serves the same static files, so a reload shows page changes there, but Python changes need the
  user to restart it.

## Long-job warning (2026-10-22)
- estimates.py: seconds per item = median of the last 15 recorded runs per job kind (and TRELLIS
  mode) in `$AP_ROOT/logs/job_times.jsonl` (the queue records every successful job), else the
  stages' own timings in the projects' runs.jsonl, else DEFAULTS (said so in the dialog).
- The UI asks the job's own endpoint first (`X-AP-Estimate: 1`): the handler runs its checks and
  counts and stops at jobs.submit (EstimateOnly). Only ESTIMATE_ROUTES (app.py) may be asked:
  routes that write before they submit (rebuild, guide/apply, shot camera, assembly, restore) are
  left out. Over 10 min api() shows the long-job dialog; Library › Areas batches show the same
  lines in their own confirm and pass {confirmed: true}. A canceled start throws "Not started.",
  which alert() ignores.
- Jobs the panel can't count show their time left from the job's est_s.

## Version actions refresh the row (2026-10-23)
- Every version action (Delete, Use this version, a cleanup step, Remove old versions) and a finished
  Make 3D / Reroll / rebuild / restore / Clean up job goes through `versionsChanged(asset, onChange)` (versions.js):
  it refetches the listing into VER_CACHE, bumps `ROW_REV` (part of the Generate 3D Model & Review row's sig, so the
  row is rebuilt), runs onChange, then redraws every `.version-strip` of the asset. Before, a Keep (gone
  since 2026-10-27) or a delete that changed no model card left the row and its strip stale until a reload.
- `versionsAfterLoad()` (called in `load()`) spots version jobs finished since the last load.
- Test: `tests/test_ui_versions_refresh.py` (browser); `_browse(..., seed=fn)` adds project data.

## Automatic values are real text (2026-10-24)
- Fields with an automatic value use `autoFieldHtml` / `autoFieldsWire` / `autoFieldValue` (shell.js): the
  automatic value is the field's editable text with an "automatic" / "edited" mark and Reset to auto, never a
  placeholder (it vanished on click). Unchanged or empty saves as automatic (null). Used by Manage entry's Name
  and Description and the brief form's Storey and Bay (`state: false`: that form has its own source marks).
- The brief form's Storey placeholder ignored `measured.storey_h`, which the builder takes first.
- Manage entry's Save says what changed fields affect (`entrySavedNotice`): the description only reaches the
  next reference sheets (sw_library.asset_fields -> s2_refs); form and size the next unguided Reroll or Brief &
  build; the brief, guided Reroll and Make 3D never read the description.
- Test: tests/test_ui_auto_fields.py (browser).

## Readability pass (2026-10-25)
- Help text is one line plus a "?" (`helpQ(full)` in shell.js; `.tab-help`): the full text is the
  "?"'s tooltip, and a click opens it in a window. Every tab's header line is `STEPS[].short`, its
  `desc` behind the "?". Wordy card notes go through `shortNote` (first words, the rest as tooltip).
  Nothing is removed, only moved. `tests/test_ui_readability.py` fails on any `.hint` in index.html
  longer than one line (80 characters).
- Library step strip (`#lib-steps`, `libSteps()` in workflow.js): Decide → Sheets → Choose Reference (id `star`) → Build →
  Approve, each asset counted at the first step it hasn't done; the first step with work waiting is
  highlighted, and each step jumps to where it happens.
- The step strip does the work (2026-10-25): it is on Library and on Generate 3D Model & Review; each step goes where it happens
  with the list filtered to what waits there (Asset review Not decided; Reference sheets No sheets yet / Sheets, none
  starred; Generate 3D Model & Review Needs a view picked / Built, not approved). 2 Sheets and 4 Build offer their batches in a bar
  under the strip (`.lib-offer`), counted by `/areas/batch` with `everywhere: true` (the whole library), plus Extract
  views and "N need a view picked". The filters use `libStepOf()` (workflow.js), so their counts match the strip's.
  Test: tests/test_ui_lib_step_strip.py.
- Help › Show guide is a small floating window (`.side.guide`, position fixed), off by default.
- Never find a test server with a loop over /proc cmdlines from the shell: the loop's own bash
  command line contains the pattern and it killed itself. Find it by its listening port
  (/proc/net/tcp inode -> /proc/<pid>/fd).

## Background jobs and the poll (2026-10-25)
- `load()` runs one at a time (app.js): a load asked for while one runs waits and runs once more. The 2 s
  poll used to stack loads on a slow server (3–4 in flight), each rendering the whole page.
- The poll (`pollLoad`) logs a failed load and keeps going; `render()` and `renderShell()` run each part
  through `guarded()` (net.js), and the Jobs panel and job window draw first, so a redraw that throws
  never freezes them. `jobDone` retries failed checks. Run in background clears a pending reopen.
- Test: `tests/test_ui_background_jobs.py` (browser; stubbed job, slow server, failed polls, a throwing redraw).

## Fixable job errors (2026-10-26)
- A known job error whose fix is another option raises `jobs.Fixable(plain message, fix, detail=raw)`: the job's
  `error` is the plain message, `fix` ({action, label, ...}) becomes a button under it in the job window, the Jobs
  panel and the job notice (`jobFixButton`, loaders.js; actions in `JOB_FIXES`), and the raw error goes to the log only.
- `app._job_error` maps them for Brief & build, Reroll and guidance: `library_entries.NoFrontView` (Auto view
  failed; carries the scores) -> "Try another view to measure", which reopens Brief & build with View to measure on
  the best view and each view's score in the labels; `FormNeeded` -> "Choose the form and size" (the form fields).
- Test: tests/test_ui_job_fixes.py (API cases, plus a browser case).

## Generate 3D Model & Review row layout; Keep removed (2026-10-27)
- Per asset, top to bottom: header (thumb, name, id, Active vN, Manage entry…, size; right: the
  game/cinematic/hero toggle (stored game/cine/hero), New seed, Make 3D, so the tier is set before building); the review line
  right-aligned (status, Approve, Re-cut views…, Needs changes); the cut views in a "Cut Sheets"
  group box; then Versions (left) beside the 3D model cards (right). The cards column has an 8 px
  top margin so the cards start on the Versions box's border line (it runs through the middle of
  the 16 px legend); below 1100 px the two stack.
- Version rows (versions.js `verSteps`) carry the cleanup steps: a button while a step is to do, a
  grey tick once done, nothing when it doesn't apply (procedural: no Smooth / Remove ground). The
  listing gives `glb`, `cleaned` (for the asset's current tier), `smoothed`/`smoothable`,
  `ground_checked`/`groundable` and the asset's `usage`. Clean up posts that version's `glb`; the
  job's tag carries it, so the row shows Cleaning….
- Use this version / Delete moved to the model cards; `versionStrip(..., {manage: true})` (Asset
  review's detail view, no cards) keeps them on the rows. Keep / pin is gone (UI, API, versions.py);
  versions pinned before stay protected and still ask before Delete.

## Next city build per asset; Ready for Unreal (2026-10-27)
- `nextBuildData` (app.js `loadNextBuild`, every load) is `GET .../next-build?use_active=` for the Unreal
  tab's Use Active versions tickbox; ticking it refetches and redraws Ready for Unreal (unreal.js
  `renderReadiness`, redrawn only when its data changes) and the Generate 3D Model & Review rows (their sig has the
  asset's row). Generate 3D Model & Review shows `line` under each library asset's header.
- Ready for Unreal names are `.jump-link[data-jump="views"]`: jump.js shows the Generate 3D Model & Review tab
  (showTab, not showSub) and jumps with the usual highlight.

## Stop jobs frees the GPUs (2026-10-26)
- `POST /api/jobs/stop_all`: `procs.snapshot()` (the vision model server from vlm.state(), plus the TRELLIS
  and headless Unreal processes jobs registered with `procs.track`, each with the VRAM its process group
  holds), then every active job is canceled, then `procs.stop` ends what is left and reports per GPU
  ("Freed 27 GB on GPU 0: vision model."). The snapshot comes first: a cancel already kills TRELLIS
  and Unreal, and their memory would read 0. ComfyUI is never touched (its jobs get the usual interrupt).
- `/status` carries `loaded` (what Stop jobs would end), so the button is enabled with a model loaded and
  no job running. Test: tests/test_stop_all.py.

## No CDN: vendored browser libraries (2026-10-31)
- `static/vendor/three` (r169) and `static/vendor/model-viewer` (4.4.0, its own bundled three.js): the UI works
  offline. Every `<script src>` in index.html is local (tests/test_ui_static.py). model-viewer would still
  reach gstatic.com for Draco / KTX2 decoders, but only for compressed GLBs, which the pipeline never writes.

## Layout tab: City in 3D (2026-10-31)
- `city_view.py` turns the plan into the viewer's scene (buildings `[x y z w d h rot type district]`, draped
  ways, park / paving rings, the heights decimated to 257): written as `site/city3d.json` by `plan_city`, made
  again on `GET .../site/city3d` when its `sig` (layout, greybox, built slots, heightmap mtimes) changed. Re-read
  .blend's hand edits are applied per slot (`sw_site.slot_moves`; `city_view.slot_id` = `add_slots`' grouping).
- `city3d.js` loads three.js r169 from `static/vendor/three` by dynamic `import()` (a classic script, so the
  `node --check` of all scripts still works; vendor/ isn't in that cat). One InstancedMesh per type, merged
  ribbons and polygons, Z-up; OrbitControls for the 3D camera and a second one (no rotate) for the top-down
  orthographic view. It redraws when the status' plan / greybox / layout mtimes change, keeping the camera.
- Headless Chrome needs `--enable-unsafe-swiftshader --use-angle=swiftshader` for WebGL (tests/test_ui_city3d.py);
  its frame times are software GL, not a GPU's. No WebGL: the plan image in the same box.
- The Layout tab scrolls (2026-10-31): viewer, catalog (`.city`, at least 460 px, filling what's left), roll group
  stack with `flex-shrink: 0`. With the tab `overflow: hidden` the catalog was the only part that could shrink,
  and its list's 120 px floor overflowed under the roll group (its rows unclickable).
