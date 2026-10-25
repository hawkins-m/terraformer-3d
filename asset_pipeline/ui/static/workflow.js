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
  {id: "views", tabs: ["views"], name: "Views and 3D", short: "Pick views and make the 3D models.", desc: "extract reference views from starred sheets and make 3D models (TRELLIS) for props and irregular assets."},
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
    const ents = libEntries(), dec = e => libDecision(e);
    const undecided = ents.filter(e => !dec(e)).length, included = ents.filter(e => dec(e) && dec(e) !== "exclude");
    const sheeted = new Set(refs.filter(u => u.plan === "library" && u.sheets.length).map(u => u.key));
    const builds = (lr.list && lr.list.assets) || [], approved = builds.filter(b => b.status === "approved").length;
    const noSheet = included.filter(e => !sheeted.has(e.id) && !lrAssetsOf(e.id).length).length;
    const st = run ? "running" : missing.length ? "stale" : undecided ? "input" : noSheet ? "ready"
      : builds.length && approved < builds.length ? "review" : included.length ? "done" : "input";
    const detail = missing.length ? `${plural(missing.length, "locked shot")} not in the asset plan (${missing.map(r => r.id).join(", ")}): Discover assets again`
      : [undecided && `${undecided} of ${plural(ents.length, "asset")} not decided`, noSheet && `${noSheet} without reference sheets`,
        builds.length && `${approved} of ${plural(builds.length, "build")} approved`].filter(Boolean).join(" · ") || `${plural(included.length, "asset")} decided`;
    return {status: st, detail, next};
  },
  views() {
    const r = reviewData;
    if (!r) return {status: "none", detail: "", next: {ok: false, reason: "Loading…"}};
    const withViews = r.assets.filter(a => a.views.length), with3d = r.assets.filter(a => a.results.length);
    const waiting = withViews.filter(a => !a.results.length).length;
    const unapproved = with3d.filter(a => a.review != null ? a.review !== "approved" : false).length;
    const st = anyJob(["views.cut", "3d.trellis", "cleanup"]) ? "running" : !r.starred_sheets && !with3d.length ? "none"
      : r.pending_sheets.length ? "ready" : waiting ? "input" : unapproved ? "review" : with3d.length ? "done" : "ready";
    const detail = st === "none" ? "star reference sheets in Library › Reference sheets first"
      : [r.pending_sheets.length && `${plural(r.pending_sheets.length, "starred sheet")} to extract views from`,
        waiting && `${plural(waiting, "asset")} with views but no 3D model`, unapproved && `${unapproved} not approved`]
        .filter(Boolean).join(" · ") || `${plural(with3d.length, "asset")} with a 3D model`;
    return {status: st, detail, next: {ok: !!siteData.summary, reason: "Build the greybox first (Greybox)."}};
  },
  unreal() {
    const u = unrealData;
    if (!u) return {status: "none", detail: "", next: {ok: false}};
    const run = (data.jobs || []).some(j => isActive(j) && j.kind.startsWith("ue."));
    const st = run ? "running" : !u.greybox ? "none" : !u.export ? "ready" : ueExportStale() ? "stale" : !u.project ? "input" : "done";
    const detail = !u.greybox ? "build the greybox first (Greybox)" : !u.export ? "export the greybox and library for Unreal"
      : ueExportStale() ? "the greybox changed after the export: export again" : !u.project ? "create the UE project"
      : "exported, UE project ready: import, render and match the look";
    return {status: st, detail, next: {ok: false, reason: "This is the last step."}};
  },
};

// [{...step, status, detail, next}] for the whole workflow, from the data already loaded
function workflow() {
  if (!data || !siteData) return [];
  return STEPS.map(s => {
    let r;
    try { r = STEP_STATE[s.id](); } catch (err) { r = {status: "none", detail: "", next: {ok: false, reason: ""}}; console.warn(s.id, err); }
    return {...s, ...r};
  });
}

// --- empty states: the real missing prerequisite for the library (Reference sheets, Views and 3D) ---
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
  {id: "sheets", name: "Sheets", go: ["library", "refs"], tip: "Included assets with no reference sheet yet (Library › Reference sheets)."},
  {id: "star", name: "Star", go: ["library", "refs"], tip: "Assets with sheets but none starred (Library › Reference sheets)."},
  {id: "build", name: "Build", go: ["views", null], tip: "Assets with a starred sheet and no 3D model yet (Views and 3D: Make 3D or Reroll)."},
  {id: "approve", name: "Approve", go: ["views", null], tip: "Built assets not approved yet (Views and 3D › Approve)."},
];
function libSteps() {
  const out = Object.fromEntries(LIB_STEPS.map(s => [s.id, 0]));
  const decs = (libraryData && libraryData.decisions) || {}, stars = (data && data.stars) || {};
  const sheets = {};
  for (const u of refs || []) if (u.plan === "library") sheets[u.key] = u.sheets || [];
  const rows = {};
  for (const a of (reviewData && reviewData.assets) || []) if (a.plan === "library") rows[a.asset.id] = a;
  for (const e of libEntries()) {
    if (e.kind === "vegetation") continue;
    const d = libDecision(e);
    if (!d) { out.decide++; continue; }
    if (d === "exclude") continue;
    const ids = d === "kit" ? Object.keys((decs[e.id] || {}).pieces || {}).map(p => `${e.id}-${p}`) : [e.id];
    for (const id of ids) {
      const r = rows[id], built = !!(r && (r.results.length || r.current)), sh = sheets[id] || [];
      if (built) { if (r.review !== "approved") out.approve++; continue; }
      out[!sh.length ? "sheets" : !sh.some(k => stars[k]) ? "star" : "build"]++;
    }
  }
  return LIB_STEPS.map(s => ({...s, waiting: out[s.id]}));
}
function renderLibSteps() {
  const el = $("#lib-steps");
  if (!el) return;
  if (!libraryData || !libraryData.plan) { el.hidden = true; return; }
  const steps = libSteps(), first = steps.find(s => s.waiting);
  const sig = JSON.stringify(steps.map(s => s.waiting));
  if (el.dataset.sig === sig && !el.hidden) return;
  el.dataset.sig = sig; el.hidden = false;
  el.innerHTML = steps.map((s, i) => `${i ? `<span class="ls-arrow" aria-hidden="true">→</span>` : ""}<button type="button"
      class="ls-step${s.waiting ? " waiting" : ""}${s === first ? " current" : ""}" data-ls="${s.id}" title="${esc(s.tip)}"
      aria-label="${i + 1} ${s.name}: ${s.waiting} waiting">${i + 1} ${s.name}${s.waiting ? ` <span class="ls-n">${s.waiting}</span>` : " ✓"}</button>`).join("");
  el.querySelectorAll("[data-ls]").forEach(b => b.onclick = () => {
    const s = LIB_STEPS.find(x => x.id === b.dataset.ls);
    showTab(s.go[0]); if (s.go[1]) showSub(s.go[1]);
    if (s.id === "decide" && +b.querySelector(".ls-n")?.textContent && $("#lib-filter")) {   // show what waits: the undecided
      const f = $("#lib-filter"); f.value = "todo"; f.dispatchEvent(new Event("change"));
    }
  });
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
