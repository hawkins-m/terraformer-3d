// The tabs as one linear workflow: what each step is, an honest status computed from the project's
// real state (files, jobs, decisions; never from one file existing), and what the next step needs.
// Used by the header strip on every tab, the Stages list (shell.js) and the guide panel.
// Statuses, in the words the UI shows:
const ST = {none: "Not started", input: "Needs input", ready: "Ready", running: "Running", review: "Needs review",
  stale: "Out of date", done: "Complete"};

// Steps in tab order. Layout (tab id "city") sets the rules and generates where every building slot
// goes; Greybox (tab id "site") builds the block-out from it, which may then be edited in Blender.
const STEPS = [
  {id: "style", tabs: ["style"], name: "Style", short: "Explore looks; save a style anchor (optional).", desc: "explore looks and save a style anchor (optional: frames use its style text)."},
  {id: "layout", tabs: ["city"], name: "Layout", short: "Set the rules and generate the layout.", desc: "set the rules and generate the layout (where every building slot goes)."},
  {id: "greybox", tabs: ["site"], name: "Greybox", short: "Build the block-out from the layout.", desc: "build the block-out from the layout; optionally edit it in Blender and re-read it (the .blend then becomes the source of truth)."},
  {id: "shots", tabs: ["shots"], name: "Shots", short: "Render each shot camera's passes.", desc: "render each shot camera's depth, edge and object-id passes from the greybox."},
  {id: "frames", tabs: ["frames"], name: "Frames", short: "Generate frames; lock one per shot.", desc: "generate concept frames per shot and lock the one to use for each shot."},
  {id: "library", tabs: ["library"], name: "Library", short: "Decide, sheet and build each asset.", desc: "discover the assets the locked shots show, decide how each is made, and build them."},
  {id: "views", tabs: ["views"], name: "Generate 3D Model & Review", short: "Pick views and make the 3D models.", desc: "extract reference views from starred sheets and make 3D models (TRELLIS) for props and irregular assets."},
  {id: "unreal", tabs: ["unreal"], name: "Unreal", short: "Export, import, render, match the look.", desc: "export, import into Unreal, render the shots and match the look to the approved frames."},
];
const stepOfTab = tab => STEPS.find(s => s.tabs.includes(tab));
const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
const anyJob = kinds => kinds.some(k => activeJobs(k).length);

// Each returns {status (an ST key), detail, next: {ok, reason}}; next is what the following step needs.
const STEP_STATE = {
  style() {
    const a = data.project.anchor, scenes = data.explore.length, board = Object.keys(data.moodboard || {}).length;
    const st = anyJob(["style.explore", "style.derive", "style.draft"]) ? "running"
      : a && ((a.images || []).length || a.style_text) ? "done" : scenes || board ? "review" : "none";
    const detail = st === "done" ? ((a.images || []).length ? `anchor saved: ${plural(a.images.length, "image")}${a.style_text ? " and style text" : ""}` : "style text saved (no anchor images)")
      : st === "review" ? "star scenes or objects, then save the style anchor (or just the style text)"
      : st === "none" ? "optional: skip it to work without a style anchor" : "";
    // an anchor of style text alone is complete, and says so
    return {status: st, detail, next: {ok: true}, label: st === "done" && !(a.images || []).length ? "Complete (text only)" : null};
  },
  layout() {
    // a city: generated from the catalog by Update city plan (site/city_plan.png); a project with its own
    // layout.json (the starter layout or one made in Blender) has no plan to generate
    const s = siteData, o = outOfDate();
    const planned = !!(typeof statusData !== "undefined" && statusData && statusData.plan_mtime);
    const st = anyJob(["site.plan"]) ? "running" : !s.layout ? "input"
      : !isCity() ? "done" : o.plan ? "stale" : planned ? "done" : "ready";
    const detail = !s.layout ? "start from the starter city here, or from the starter layout in the Greybox tab to lay it out in Blender"
      : !isCity() ? "your own layout (site/layout.json): edit it in Blender from the Greybox tab"
      : o.plan ? "the catalog changed after the last plan: Update city plan" : planned ? "city plan up to date" : "generate the city plan";
    return {status: st, detail, next: {ok: !!s.layout, reason: "Start a layout first."}};
  },
  greybox() {
    const s = siteData, o = outOfDate(), sum = s.summary;
    const run = anyJob(["site.build", "site.extract", "site.preview"]) || applyRunning();
    const stale = sum && (sum.stale || o.greybox);
    const st = run ? "running" : !s.layout ? "none" : !sum ? "ready" : stale ? "stale" : "done";
    const detail = !s.layout ? "no layout yet (Layout), or start from the starter layout here"
      : !sum ? "layout ready: build the greybox"
      : stale ? [o.greybox && "the catalog changed after the last build",
        sum.stale && "the .blend changed since it was last read (Re-read .blend)"].filter(Boolean).join("; ")
      : `greybox built: ${plural(sum.slots, "slot")}${sum.edited ? ", edited in Blender" : ""}`;
    return {status: st, detail, next: {ok: !!sum, reason: "Build the greybox first."}};
  },
  shots() {
    const sh = siteData.shots || [], bad = sh.filter(r => !r.rendered || r.stale), fresh = sh.length - bad.length;
    const st = !sh.length ? "none" : anyJob(["shots.render", "shots.camera"]) ? "running"
      : !sh.some(r => r.rendered) ? "ready" : bad.length ? "stale" : "done";
    const detail = !sh.length ? "no shots yet: build the greybox (Greybox)"
      : st === "ready" ? `${plural(sh.length, "shot")} to render` : bad.length ? `${bad.length} of ${plural(sh.length, "shot")}: passes missing or stale`
      : `${plural(sh.length, "shot")}, every pass current`;
    return {status: st, detail, next: {ok: fresh > 0, reason: "Render the shot passes first."}};
  },
  frames() {
    const rows = framesData ? framesData.shots : [], real = rows.filter(r => !r.mood);
    const locked = real.filter(r => r.locked), withFrames = rows.filter(r => r.batches.length);
    const unreviewed = rows.flatMap(r => r.batches.filter(b => !b.archived).flatMap(b => b.frames)).filter(f => !f.role).length;
    const st = !rows.length ? "none" : anyJob(["frames.generate"]) ? "running"
      : real.length && locked.length === real.length ? "done" : !withFrames.length && !locked.length ? "ready" : "review";
    const detail = !rows.length ? "no shots yet" : st === "ready" ? "generate frames for the shots"
      : `${locked.length} of ${plural(real.length, "shot")} locked${unreviewed ? ` · ${plural(unreviewed, "frame")} not reviewed` : ""}`;
    return {status: st, detail, next: {ok: locked.length > 0, reason: "Lock at least one shot (Use this frame for this shot)."}};
  },
  library() {
    const plan = libraryData && libraryData.plan, run = anyJob(["library.plan", "library.check", "refs.generate",
      "library.brief_build", "library.rebuild", "library.guide", "library.reroll"]);
    const lockedFrames = (framesData ? framesData.shots : []).filter(r => r.locked && r.locked !== "greybox" && !r.mood);
    const starred = reviewData ? reviewData.starred_sheets : 0;
    const next = {ok: starred > 0 || !!(reviewData && reviewData.assets.some(a => a.results.length)),
      reason: "Star at least one reference sheet (Library › Reference sheets)."};
    if (!plan) return {status: run ? "running" : lockedFrames.length ? "ready" : "none", next,
      detail: lockedFrames.length ? "discover the assets the locked shots show" : (libraryPrereq() || {text: ""}).text.replace(/^No assets yet: /, "")};
    // a frame locked after the plan, or a different frame now locked: the plan never considered it
    // (frames it considered and dropped as an older layout are in candidates too, and stay dropped)
    const considered = new Set((plan.candidates || []).map(c => c.frame));
    const missing = lockedFrames.filter(r => !considered.has(r.locked));
    // per asset, the strip's own step (libStepOf): the label and Next read the same counts
    const ents = libEntries(), at = libWaiting();
    const included = ents.filter(e => libDecision(e) && libDecision(e) !== "exclude");
    const refsReady = !at.decide && !at.sheets && !at.star && included.length > 0;
    const st = run ? "running" : missing.length ? "stale" : at.decide || !included.length ? "input" : at.sheets ? "ready"
      : at.star ? "input" : "done";
    const detail = missing.length ? `${plural(missing.length, "locked shot")} not in the asset plan (${missing.map(r => r.id).join(", ")}): Discover assets again`
      : !included.length && !at.decide ? "no asset included: decide each asset in Asset review"
      : [at.decide && `${at.decide} of ${plural(ents.length, "asset")} not decided`, at.sheets && `${plural(at.sheets, "asset")} without reference sheets`,
        at.star && `${plural(at.star, "asset")} with no chosen reference (star a sheet)`].filter(Boolean).join(" · ")
        || `every included asset decided, with a chosen reference (${plural(included.length, "asset")})`;
    return {status: st, detail, label: st === "done" ? "References ready" : null,
      next: refsReady ? {ok: true} : next};
  },
  views() {
    const r = reviewData;
    if (!r) return {status: "none", detail: "", next: {ok: false, reason: "Loading…"}};
    const withViews = r.assets.filter(a => a.views.length), with3d = r.assets.filter(a => a.results.length);
    // library assets by the strip's step: complete only when every included one is approved
    const at = libWaiting(), lib = libraryData && libraryData.plan;
    const other = r.assets.filter(a => a.plan !== "library"), otherWaiting = other.filter(a => !a.results.length).length;
    const pre = lib ? (at.decide || 0) + (at.sheets || 0) + (at.star || 0) : 0;
    const building = (at.build || 0) + otherWaiting, approving = at.approve || 0;
    const any = with3d.length || Object.values(at).some(Boolean);
    const st = anyJob(["views.cut", "3d.trellis", "cleanup"]) ? "running" : !r.starred_sheets && !with3d.length && !building ? "none"
      : r.pending_sheets.length ? "ready" : building ? "input" : approving ? "review" : pre ? "input"
      : any && (lib || other.length) ? "done" : "ready";
    const detail = st === "none" ? "star reference sheets in Library › Reference sheets first"
      : [r.pending_sheets.length && `${plural(r.pending_sheets.length, "starred sheet")} to extract views from`,
        building && `${plural(building, "asset")} with no 3D model yet`, approving && `${approving} not approved`,
        pre && `${plural(pre, "asset")} still waiting in Library (decide, sheets, choose a reference)`]
        .filter(Boolean).join(" · ") || `${plural(with3d.length, "asset")} with a 3D model, every one approved`;
    return {status: st, detail, label: st === "review" ? `Awaiting approval (${approving})` : null,
      next: {ok: !!siteData.summary, reason: "Build the greybox first (Greybox)."}};
  },
  unreal() {
    // the detailed panel's own steps (ueSteps): Exported -> Scene assembled (imported or built) ->
    // Render verified (shots rendered after the import and a look match run); Complete only there
    const u = unrealData;
    if (!u) return {status: "none", detail: "", next: {ok: false}};
    const run = (data.jobs || []).some(j => isActive(j) && j.kind.startsWith("ue."));
    const k = ueSteps(u), exported = !!u.export && !ueExportStale(), assembled = exported && k.import[1];
    const verified = assembled && k.render[1] && k.look[1];
    const st = run ? "running" : !u.greybox ? "none" : !u.export ? "ready" : ueExportStale() ? "stale"
      : verified ? "done" : assembled ? "review" : "input";
    const label = st === "done" ? "Render verified" : st === "review" ? "Scene assembled" : st === "input" ? "Exported" : null;
    const detail = !u.greybox ? "build the greybox first (Greybox)" : !u.export ? "build the city in Unreal (or export the greybox)"
      : ueExportStale() ? "the greybox changed after the export: build or export again"
      : !u.project ? "exported: create the UE project, then import (Build city in Unreal does both)"
      : !assembled ? "exported: build the city in Unreal (or import) to assemble the scene"
      : !k.render[1] ? "scene assembled: render the shots" : !k.look[1] ? "shots rendered: match the look to an approved frame"
      : "rendered and look matched";
    return {status: st, detail, label, next: {ok: false, reason: "This is the last step."}};
  },
};

// The Unreal tab's numbered steps (unreal.js draws them; the Unreal stage's status reads them): each button is enabled only when its step can run, and says why
// not beside it. [state text, done?, reason it can't run ("" = it can)].
function ueSteps(u) {
  const exp = u.export, imp = u.imported, ren = u.renders, looks = u.looks || [];
  const when = t => new Date(t * 1000).toLocaleString();
  const fresh = exp && !ueExportStale();
  return {
    init: [u.project ? `Created: ${u.uproject}` : "", !!u.project, u.project ? "Already created." : ""],
    export: [null, !!fresh, !u.greybox ? "Build the greybox first (Greybox)." : ""],
    import: [imp ? `Imported ${when(imp.mtime)}${imp.ok ? "" : " (with errors)"}${exp && imp.mtime < exp.mtime ? " · the export is newer: import again" : ""}` : "",
      !!(imp && exp && imp.mtime >= exp.mtime && imp.ok),
      !u.project ? "Create the UE project first (step 1)." : !exp ? "Export first (step 2)." : u.editor_open ? "Close the Unreal Editor first." : ""],
    render: [ren ? `${ren.count} shot render${ren.count === 1 ? "" : "s"}, newest ${when(ren.mtime)}` : "",
      !!(ren && imp && ren.mtime >= imp.mtime), !imp ? "Import first (step 3)." : ""],
    look: [looks.length ? `${looks.length} look preset${looks.length === 1 ? "" : "s"}: ${looks.map(l => `${l.name}${l.runs ? ` (${l.runs} run${l.runs === 1 ? "" : "s"})` : ""}`).join(", ")}`
      : "No look preset yet: one is created from an approved shot.", looks.some(l => l.runs),
      !imp ? "Import first (step 3)." : !looks.length && !(u.approved_shots || []).length ? "Lock a shot on its approved frame first (Frames)." : ""],
  };
}

// [{...step, status, detail, next}] for the whole workflow, from the data already loaded
// One rule for every step with a following one: it is never Complete while its Next button still
// asks for something (the label and Next come from the same state). Such a step needs input, and
// says what Next asks for.
function workflow() {
  if (!data || !siteData) return [];
  return STEPS.map((s, i) => {
    let r;
    try { r = STEP_STATE[s.id](); } catch (err) { r = {status: "none", detail: "", next: {ok: false, reason: ""}}; console.warn(s.id, err); }
    if (r.status === "done" && i < STEPS.length - 1 && !r.next.ok)
      r = {...r, status: "input", label: null, detail: [r.detail, r.next.reason].filter(Boolean).join(" · ")};
    return {...s, ...r};
  });
}

// {decide, sheets, star, build, approve: how many assets wait there} (the Library strip's counts)
function libWaiting() {
  const out = {};
  if (!libraryData || !libraryData.plan) return out;
  for (const st of Object.values(libStepOf())) if (st) out[st] = (out[st] || 0) + 1;
  return out;
}

// --- empty states: the real missing prerequisite for the library (Reference sheets, Generate 3D Model & Review) ---
// {text, go: [tab, sub], label}: world/city projects go greybox → locked frame → Discover assets →
// decisions → sheets; a project with no site layout (scene mode) still starts from Scene plans.
function libraryPrereq(views = false) {
  const go = (text, tab, sub, label) => ({text, go: [tab, sub], label});
  if (!siteData || !siteData.layout) return go("No assets yet: analyse a scene in Library › Scene plans.", "library", "plan", "Open Scene plans");
  if (!siteData.summary) return go("No assets yet: build the greybox first.", "site", null, `Open ${TAB_NAMES.site}`);
  const locked = (framesData ? framesData.shots : []).filter(r => r.locked && r.locked !== "greybox" && !r.mood);
  if (!locked.length) return go("No assets yet: lock a shot on its approved frame (Frames › Use this frame for this shot).", "frames", null, "Open Frames");
  if (!libraryData || !libraryData.plan) return go("No assets yet: discover them from the locked shots (Library › Discover assets from locked shots).", "library", "assets", "Open Asset review");
  const ents = libEntries(), included = ents.filter(e => libDecision(e) && libDecision(e) !== "exclude");
  if (!included.length) return go("No assets included yet: decide each asset in Library › Asset review.", "library", "assets", "Open Asset review");
  if (views) return go("No views yet: star reference sheets in Library › Reference sheets, then Extract reference views.", "library", "refs", "Open Reference sheets");
  return null;
}
// the hint as HTML with a button that goes there (wired by wireGo)
// (data-go-*, never data-tab / data-sub: showTab and showSub hide every element carrying those)
const prereqHtml = p => `${esc(p.text)} <button type="button" class="small prereq-go" data-go-tab="${p.go[0]}" data-go-sub="${p.go[1] || ""}">${esc(p.label)}</button>`;
function wireGo(el) {
  el.querySelectorAll(".prereq-go").forEach(b => b.onclick = () => { showTab(b.dataset.goTab); if (b.dataset.goSub) showSub(b.dataset.goSub); });
}

// --- guide (Help › Show guide): a small floating step list, off unless the viewer turns it on -------
// Each step ticks itself from the same states as the headers; Go opens its tab; what a step is and
// its state are the tooltips. The choice is a per-browser convenience (localStorage), never forced on.
const guideOn = () => { try { return localStorage.getItem("ap.guide") === "1"; } catch (_) { return false; } };
function toggleGuide(on = !guideOn()) {
  try { localStorage.setItem("ap.guide", on ? "1" : "0"); } catch (_) { /* private window: this page only */ }
  $("#guide").dataset.on = on ? "1" : "0";
  renderGuide();
}
function renderGuide() {
  const el = $("#guide");
  if (!el) return;
  const on = el.dataset.on ? el.dataset.on === "1" : guideOn();
  el.hidden = !on || !slug || !data;
  if (el.hidden) return;
  const wf = workflow();
  const nextUp = wf.find(s => s.status !== "done" && !(s.id === "style" && s.status === "none"));
  const sig = JSON.stringify([currentTab, wf.map(s => [s.status, s.label, s.detail]), nextUp && nextUp.id]);
  if (el.dataset.sig === sig) return;
  el.dataset.sig = sig;
  el.innerHTML = `<div class="wtitle"><span class="wt">Guide</span><button type="button" class="wclose" id="guide-close" aria-label="Hide the guide" title="Help › Show guide turns it back on">x</button></div>
      <ol class="guide-steps">${wf.map((s, i) => {
        const tab = s.tabs[0];
        return `<li class="${s.status === "done" ? "done" : ""}${nextUp && s.id === nextUp.id ? " next" : ""}${s.tabs.includes(currentTab) ? " here" : ""}" title="${esc(`${s.short} ${s.detail || ""}`.trim())}">
          <span class="gcheck" aria-hidden="true">${s.status === "done" ? "☑" : "☐"}</span>
          <span><b>${i + 1}. ${esc(s.name)}</b> <span class="st-chip st-${s.status}">${esc(s.label || ST[s.status])}</span></span>
          <button type="button" class="small guide-go" data-go-tab="${tab}" ${s.tabs.includes(currentTab) ? "disabled" : ""}
            aria-label="Go to ${esc(TAB_NAMES[tab])}">${s.tabs.includes(currentTab) ? "Here" : "Go"}</button></li>`; }).join("")}</ol>
      <p class="hint" style="margin:4px 0 0">${nextUp ? `Next up: <b>${esc(nextUp.name)}</b>: ${esc(nextUp.short)}` : "Every step is complete."}</p>`;
  el.querySelectorAll(".guide-go").forEach(b => b.onclick = () => showTab(b.dataset.goTab));
  $("#guide-close", el).onclick = () => toggleGuide(false);
}

// --- Library step strip: Decide → Sheets → Star → Build → Approve, with what waits at each ---------
// Counts per asset (an entry, or each module of a kit decision); vegetation is Unreal foliage and
// never waits. An asset waits at the first step it hasn't done: no decision, no sheet, no starred
// sheet, no 3D model, not approved. A model built without sheets (a brief) has passed 2 and 3.
const LIB_STEPS = [
  {id: "decide", name: "Decide", go: ["library", "assets"], tip: "Assets without a decision (Library › Asset review)."},
  {id: "sheets", name: "Sheets", go: ["library", "refs"], tip: "Decided assets that will be built and have no reference sheet yet, Description only ones included (Library › Reference sheets)."},
  {id: "star", name: "Choose Reference", go: ["library", "refs"], tip: "Assets with sheets but no chosen reference: star the sheet to build from (Library › Reference sheets)."},
  {id: "build", name: "Build", go: ["views", null], tip: "Assets with a starred sheet and no 3D model yet (Generate 3D Model & Review: Make 3D or Reroll)."},
  {id: "approve", name: "Approve", go: ["views", null], tip: "Built assets not approved yet (Generate 3D Model & Review › Approve)."},
];
// {asset id: the step it waits at, or null (excluded, vegetation, approved)}
// Whether an asset needs a sheet is the server's call (libraryData.sheets, areas.sheet_skip): the same
// list the sheets batch and the Reference sheets tab use, so 2 Sheets, Make sheets for all and No sheets
// yet always agree. Assets with no sheet that don't need one (a procedural brief stands in, a copy rides
// on its original, a good-source decision no frame shows) wait at Build, or nowhere.
function libStepOf() {
  const out = {};
  const decs = (libraryData && libraryData.decisions) || {}, stars = (data && data.stars) || {};
  const sh = (libraryData && libraryData.sheets) || {}, need = new Set(sh.needed || []), skip = sh.skip || {};
  const sheets = {};
  for (const u of refs || []) if (u.plan === "library") sheets[u.key] = u.sheets || [];
  const rows = {};
  for (const a of (reviewData && reviewData.assets) || []) if (a.plan === "library") rows[a.asset.id] = a;
  for (const e of libEntries()) {
    if (e.kind === "vegetation") continue;
    const d = libDecision(e);
    if (!d) { out[e.id] = "decide"; continue; }
    if (d === "exclude") { out[e.id] = null; continue; }
    const ids = d === "kit" ? Object.keys((decs[e.id] || {}).pieces || {}).map(p => `${e.id}-${p}`) : [e.id];
    for (const id of ids) {
      const r = rows[id], built = !!(r && (r.results.length || r.current)), mine = sheets[id] || [];
      out[id] = need.has(id) ? "sheets"
        : built ? (r.review !== "approved" ? "approve" : null)
        : !mine.length ? (skip[id] === "brief" || skip[id] === "built" ? "build" : null)
        : !mine.some(k => stars[k]) ? "star" : "build";
    }
  }
  return out;
}
function libSteps() {
  const out = Object.fromEntries(LIB_STEPS.map(s => [s.id, 0]));
  for (const st of Object.values(libStepOf())) if (st) out[st]++;
  return LIB_STEPS.map(s => ({...s, waiting: out[s.id]}));
}
// The strip sits on the Library tab and on Generate 3D Model & Review, so it can be followed left to right:
// each step goes where it happens with the list filtered to what waits there, and the steps with a
// batch (2 Sheets, 4 Build) offer it in a bar under the strip, counted by the batch's own dry run
// over the whole library (/areas/batch everywhere).
const libOffer = {step: null, counts: {}, seq: 0, sig: null};
function renderLibSteps() {
  const els = document.querySelectorAll(".lib-steps");
  if (!els.length) return;
  if (!libraryData || !libraryData.plan) { els.forEach(el => { el.hidden = true; }); libOfferShow(null); return; }
  const steps = libSteps(), first = steps.find(s => s.waiting);
  const sig = JSON.stringify(steps.map(s => s.waiting));
  if (libOffer.step && libOffer.sig !== sig) libOfferCount();     // what waits changed: count the batch again
  libOffer.sig = sig;
  els.forEach(el => {
    if (el.dataset.sig === sig && !el.hidden) return;
    el.dataset.sig = sig; el.hidden = false;
    el.innerHTML = steps.map((s, i) => `${i ? `<span class="ls-arrow" aria-hidden="true">→</span>` : ""}<button type="button"
        class="ls-step${s.waiting ? " waiting" : ""}${s === first ? " current" : ""}" data-ls="${s.id}" title="${esc(s.tip)}"
        aria-label="${i + 1} ${s.name}: ${s.waiting} waiting">${i + 1} ${s.name}${s.waiting ? ` <span class="ls-n">${s.waiting}</span>` : " ✓"}</button>`).join("");
    el.querySelectorAll("[data-ls]").forEach(b => b.onclick = () => libStepGo(b.dataset.ls));
  });
}
function libStepGo(id) {
  const s = LIB_STEPS.find(x => x.id === id);
  showTab(s.go[0]); if (s.go[1]) showSub(s.go[1]);
  if (s.id === "decide" && $("#lib-filter")) {          // show what waits: the undecided
    const f = $("#lib-filter"); f.value = "todo"; f.dispatchEvent(new Event("change"));
  }
  if (s.id === "sheets" || s.id === "star") refsFilterSet(s.id === "sheets" ? "missing" : "star");
  if (s.id === "build" || s.id === "approve") viewsFilterSet(s.id === "approve" ? "approve" : "all");
  libOfferShow(s.id === "sheets" || s.id === "build" ? s.id : null);
}

// --- the batch offer under the strip ----------------------------------------------------------
const LIB_OFFER_BATCHES = {
  sheets: ["Make sheets for all", "across all areas", "Two reference sheets for each decided asset that has none yet (GPU 1, ComfyUI)"],
  build: ["Build all procedural", "", "Builds each procedural asset with a brief that isn't fresh (Blender on the CPU)"],
  trellis: ["Make 3D for TRELLIS with a chosen view", "", "A TRELLIS model for each TRELLIS asset with a chosen view and no model yet (GPU 0)"],
};
const libOfferKinds = step => step === "sheets" ? ["sheets"] : step === "build" ? ["build", "trellis"] : [];
function libOfferShow(step) {
  libOffer.step = step; libOffer.counts = {};
  if (step) libOfferCount();
  libOfferRender();
}
async function libOfferCount() {
  const seq = ++libOffer.seq, step = libOffer.step;
  await Promise.all(libOfferKinds(step).map(async k => {
    let plan = null;
    try { plan = await api(`/api/projects/${slug}/areas/batch`, {action: k, everywhere: true, dry_run: true}); }
    catch (err) { plan = {error: errorText(err)}; }
    if (seq === libOffer.seq) libOffer.counts[k] = plan;
  }));
  if (seq === libOffer.seq) libOfferRender();
}
function libOfferRender() {
  const step = libOffer.step;
  document.querySelectorAll(".lib-offer").forEach(el => {
    el.hidden = !step;
    if (!step) { el.replaceChildren(); return; }
    const btn = k => {
      const [label, where, what] = LIB_OFFER_BATCHES[k], p = libOffer.counts[k];
      if (p && p.error) return `<span class="error" title="${esc(p.error)}">${esc(label)}: ${esc(p.error)}</span>`;
      const n = p ? p.count : null;
      return `<button type="button" class="small${n ? " default" : ""}" data-offer="${k}" ${n ? "" : "disabled"} title="${esc(what)}${p && p.notes.length ? `. ${esc(p.notes.join(". "))}` : ""}">${esc(label)} (${n == null ? "…" : n})${where ? ` ${where}` : ""}…</button>`;
    };
    let html = `<span class="lo-what">${LIB_STEPS.findIndex(x => x.id === step) + 1} ${LIB_STEPS.find(x => x.id === step).name}:</span> ${libOfferKinds(step).map(btn).join(" ")}`;
    if (step === "build") {
      viewsStepMap = null;
      const needView = reviewData ? reviewData.assets.filter(VIEWS_FILTERS.view[1]).length : 0;
      const pending = reviewData ? reviewData.pending_sheets.length : 0;
      if (pending) html += ` <button type="button" class="small" data-offer-extract title="Cut the views out of newly starred sheets (SAM 3.1); then pick one per asset">Extract views from ${pending} new starred sheet${pending === 1 ? "" : "s"}</button>`;
      if (needView) html += ` <button type="button" class="link" data-offer-views title="Generate 3D Model & Review, showing the TRELLIS assets with no view picked yet">${needView} need a view picked</button>`;
    }
    html += ` <button type="button" class="wclose" data-offer-close aria-label="Hide this bar">x</button>`;
    el.innerHTML = html;
    el.querySelectorAll("[data-offer]").forEach(b => b.onclick = () => libOfferRun(b.dataset.offer));
    el.querySelector("[data-offer-extract]")?.addEventListener("click", () => { showTab("views"); $("#cut-views").click(); });
    el.querySelector("[data-offer-views]")?.addEventListener("click", () => { showTab("views"); viewsFilterSet("view"); });
    el.querySelector("[data-offer-close]").onclick = () => libOfferShow(null);
  });
}
// confirm (count, the assets, the time) like an area batch, then start it; the job window opens
async function libOfferRun(kind) {
  const [label, where, what] = LIB_OFFER_BATCHES[kind], req = {action: kind, everywhere: true};
  let plan;
  try { plan = await api(`/api/projects/${slug}/areas/batch`, {...req, dry_run: true}); }
  catch (err) { alert(errorText(err)); return; }
  if (!plan.count) { libOfferCount(); return; }
  const e = plan.estimate, long = !!(e && e.long);
  const ok = await confirmDialog({title: `${label} (${plan.count})${e ? `: about ${fmtDur(e.seconds)}` : ""}`, button: long ? "Start" : "Run", id: "lib-offer-confirm",
    body: `<p>${esc(what)}${where ? `, ${esc(where)}` : ""}.</p><p><b>${plan.count}</b> to run: ${esc(plan.items.slice(0, 40).map(a => assetName(a)).join(", "))}${plan.items.length > 40 ? " …" : ""}</p>
    ${e ? (long ? longJobHtml(e) : `<p class="hint">About ${fmtDur(e.seconds)} on ${esc(e.lane_names.join(", then "))} (${esc(e.source)}).</p>`) : ""}
    ${plan.notes.map(n => `<p class="hint">${esc(n)}</p>`).join("")}<p class="hint">Finished items are skipped, so it can be run again.</p>`});
  if (!ok) return;
  try { await api(`/api/projects/${slug}/areas/batch`, req, "POST", {confirmed: true}); }
  catch (err) { alert(errorText(err)); }
  libOfferCount();
}

// --- header strip ------------------------------------------------------------------------------
function stepHead(tab) {
  const panel = $(`section.tabpanel[data-tab="${tab}"]`);
  if (!panel) return null;
  let el = $(":scope > .step-head", panel);
  if (!el) { el = document.createElement("div"); el.className = "step-head"; el.setAttribute("role", "status"); panel.prepend(el); }
  return el;
}

function renderStepHeads() {
  if (!data || !slug) return;
  const wf = workflow(), tab = currentTab, el = stepHead(tab);
  if (!el || !wf.length) return;
  const i = wf.findIndex(s => s.tabs.includes(tab)), s = wf[i], nxt = wf[i + 1];
  const behind = wf.slice(0, i).filter(x => x.status === "stale");
  const nextTab = nxt && nxt.tabs[0];
  const sig = JSON.stringify([tab, s.status, s.label, s.detail, s.next, behind.map(x => x.id), nextTab]);
  if (el.dataset.sig === sig) return;
  el.dataset.sig = sig;
  el.innerHTML = `<div class="sh-main tab-help"><span class="sh-step">This step:</span> ${esc(s.short)} ${helpQ(s.desc[0].toUpperCase() + s.desc.slice(1), s.name)}</div>
    <span class="st-chip st-${s.status}" title="${esc(s.detail)}">${esc(s.label || ST[s.status])}</span>
    <span class="sh-detail">${esc(s.detail)}</span>
    ${behind.map(b => `<span class="sh-behind">${esc(b.name)} is out of date: <button type="button" class="link sh-go" data-go-tab="${b.tabs[0]}">revisit ${esc(TAB_NAMES[b.tabs[0]])}</button></span>`).join("")}
    <span class="spacer"></span>
    ${nxt ? `<span class="sh-reason">${s.next.ok ? "" : esc(s.next.reason || "")}</span>
      <button type="button" class="default sh-next" data-go-tab="${nextTab}" ${s.next.ok ? "" : "disabled"}
        title="${esc(s.next.ok ? `Go to ${TAB_NAMES[nextTab]}` : s.next.reason || "")}">Next: ${esc(TAB_NAMES[nextTab])} →</button>`
      : `<span class="sh-reason">${esc(s.next.reason || "")}</span>`}`;
  el.querySelectorAll("[data-go-tab]").forEach(b => b.onclick = () => showTab(b.dataset.goTab));
}
Object.assign(ACTIONS, {guide: {run: () => toggleGuide()}});
