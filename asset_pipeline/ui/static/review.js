// Stages 3-4 (Review): cut views from starred sheets, choose one per asset, tag
// game / cine / hero, and send it to 3D (TRELLIS.2 on GPU 0).

// assets whose older cleanups are shown (Show older cleanups), kept across redraws
const OLD_CLEANUPS = new Set();
const USAGES = ["game", "cine", "hero"];
const USAGE_TIPS = {game: "Game use: Clean up decimates and bakes this asset for real time.",
  cine: "Cinematic use: Clean up keeps the full mesh and textures.", hero: "Hero use: full mesh and textures, for the assets seen closest in shots."};
const ACTIVE_TIP = "This version is the one later steps (Cleanup, Unreal look scenes) will use.";
// 3D previews: the GLB's own baked textures per material slot, lit close to the Cycles thumbnails
// (Standard view, exposure -0.15). model-viewer's default (ACES at exposure 1) burnt the pale,
// sheet-tinted surfaces to white and hid material changes.
const MV_LOOK = `tone-mapping="neutral" exposure="0.75" shadow-intensity="0.8"`;
const APPROVE_TIP = "Unreal look scenes only place approved assets. Until then, export uses greybox stand-ins.";
const make3dNotes = {};        // "plan/asset" -> {message, why}: the last Make 3D queued nothing (same as a version)
const rowNotesClosed = new Set();   // reroll job ids whose report the user dismissed

// the row's note: a Make 3D that would only repeat a version, else the newest finished reroll's
// report on its guidance (what was applied, what this builder can't do)
function rowNote(a) {
  const dup = make3dNotes[`${a.plan}/${a.asset.id}`];
  if (dup) return {key: "dup", cls: "warn", html: esc(dup.message), title: dup.why || ""};
  if (a.plan !== "library" || activeJobs("library.reroll", {asset: a.asset.id}).length) return null;
  const j = (data ? data.jobs : []).filter(j => j.kind === "library.reroll" && j.tag.asset === a.asset.id && j.status === "done")
    .sort((x, y) => (y.finished || 0) - (x.finished || 0))[0];
  const r = j && j.result;
  if (!r || rowNotesClosed.has(j.id) || !r.guidance) return null;
  const rep = r.guidance_report || {};
  const head = r.version ? `Reroll v${r.version}` : `Reroll: ${r.same_as ? `same as v${r.same_as}, no new version` : "no new version"}`;
  const lines = [`<b>${esc(head)}</b> (guidance: “${esc(r.guidance)}”)`,
    `Applied: ${rep.applied && rep.applied.length ? esc(rep.applied.join("; ")) : "nothing"}${rep.small ? ` <span class="mark m-chg">Small change</span>` : ""}`];
  if (rep.not_possible && rep.not_possible.length) lines.push(`Not possible with this builder: ${esc(rep.not_possible.join(", "))}`);
  if (rep.suggest) lines.push(esc(rep.suggest));
  return {key: j.id, cls: "", html: lines.join("<br>"), title: r.message || ""};
}

// Show filter (the Library step strip sets it: 4 Build -> view, 5 Approve -> approve), by the strip's
// own step per asset (workflow.js libStepOf) so the counts match it. Needs a view: waiting at Build,
// a TRELLIS asset, no view chosen yet.
let viewsStepMap = null;              // per render
const viewsStep = a => a.plan === "library" && (viewsStepMap || (viewsStepMap = libStepOf()))[a.asset.id];
const VIEWS_FILTERS = {all: ["All assets", () => true],
  view: ["Needs a view picked", a => viewsStep(a) === "build" && !a.procedural && !a.chosen],
  approve: ["Built, not approved", a => viewsStep(a) === "approve"]};
let viewsFilter = "all";
function viewsFilterSet(k) { viewsFilter = VIEWS_FILTERS[k] ? k : "all"; renderReview(); }

function renderReview() {
  if (!reviewData) return;
  const pending = reviewData.pending_sheets.length;
  const cutJobs = activeJobs("views.cut");
  $("#cut-views").disabled = !pending || !!cutJobs.length;
  $("#cut-views").textContent = cutJobs.length ? "Extracting…" : `Extract reference views (${pending} new sheet${pending === 1 ? "" : "s"})`;
  setStop("#cut-stop", cutJobs);
  const withViews = reviewData.assets.filter(a => a.views.length).length;
  const built = reviewData.assets.filter(a => a.results.length).length;
  const pre = (!reviewData.assets.length || (!reviewData.starred_sheets && !withViews && !built)) && libraryPrereq(true);
  const sumText = pre ? prereqHtml(pre)
    : `${reviewData.starred_sheets} starred sheet(s); ${withViews} of ${reviewData.assets.length} assets have views, ${built} have a 3D model.`;
  if ($("#review-summary").dataset.sig !== sumText) {
    $("#review-summary").dataset.sig = sumText;
    if (pre) { $("#review-summary").innerHTML = sumText; wireGo($("#review-summary")); } else $("#review-summary").textContent = sumText;
  }

  viewsStepMap = null;
  const vf = $("#views-filter"), vfHtml = Object.entries(VIEWS_FILTERS).map(([k, [label, fn]]) =>
    `<option value="${k}"${k === viewsFilter ? " selected" : ""}>${esc(label)} (${reviewData.assets.filter(fn).length})</option>`).join("");
  if (vf.dataset.sig !== vfHtml) { vf.dataset.sig = vfHtml; vf.innerHTML = vfHtml; }
  const shownV = reviewData.assets.filter(VIEWS_FILTERS[viewsFilter][1]);
  // keyed: only rows whose data changed are rebuilt, keeping their loaded images (keyed.js)
  const plans = [...new Set(shownV.map(a => a.plan))];
  keyedSections($("#review-list"), plans.map(p => ({key: p, items: shownV.filter(x => x.plan === p)})), {
    heading: g => `<h3>${esc(g.key)}</h3>`,
    key: a => `${a.plan}/${a.asset.id}`,
    sig: a => JSON.stringify([a, fileVersion, assetName(a.asset.id, a.asset.name), a.plan === "library" ? verRev(a.asset.id) : 0,
      a.plan === "library" ? [variantOf(a.asset.id), variantOf(a.asset.id) && assetName(variantOf(a.asset.id)), (libraryData && libraryData.splits || {})[a.asset.id] || null] : 0,
      ["3d.trellis", "cleanup"].map(k => activeJobs(k, {plan: a.plan, asset: a.asset.id}).map(j => j.status)),
      ["views.cut", "library.brief_build", "library.reroll"].map(k => activeJobs(k, {asset: a.asset.id}).map(j => j.status)), rowNote(a)]),
    build: assetReview,
  });
  if (reviewData.assets.length && !shownV.length)
    $("#review-list").innerHTML = `<p class="hint">None: ${esc(VIEWS_FILTERS[viewsFilter][0].toLowerCase())}. Pick another Show filter.</p>`;
}
$("#views-filter").onchange = e => viewsFilterSet(e.target.value);

function assetReview(a) {
  const el = document.createElement("div"); el.className = "asset-review"; el.dataset.asset = a.asset.id;
  const x = a.asset, d = x.dimensions;
  // the header names the asset (and what it is a variant of); the version number only stands by the
  // Active badge, so "v2 canopy table copy" no longer reads as a version of canopy table
  const lib = a.plan === "library", vof = lib ? variantOf(x.id) : null;
  // the buttons follow the entry's build method (Manage entry…), not whether views exist
  const nx = a.next, noForm = !!(a.procedural && nx && nx.form && !nx.form.form);
  const places = lib && libraryData && libraryData.splits && libraryData.splits[x.id]
    ? `<span class="asset-split" title="${esc(splitTip(x.id))}">${esc(splitText(x.id))}</span>`
    : vof ? `shares ${esc(assetName(vof))}'s places` : `×${x.count}`;
  // row 1: who it is on the left; on the right everything Make 3D takes (tier, seed), then Make 3D.
  // row 2: the review. row 3: the cut views. row 4: Versions beside the 3D model cards.
  el.innerHTML = `<div class="head">
      ${a.current && a.current.thumb ? `<span class="cur-thumb" title="${ACTIVE_TIP}"><img alt="" src="${fileUrl(a.current.thumb)}"></span>` : ""}
      <span class="title">${lib ? `<b>${esc(assetName(x.id))}</b>${vof ? `<span class="variant-of"> · variant of ${esc(assetName(vof))}</span>` : ""}${assetName(x.id) !== x.id ? ` <span class="hint" style="margin:0">${esc(x.id)}</span>` : ""}` : esc(x.name)}</span>
      ${a.current ? `<span class="cur-version" title="${ACTIVE_TIP}"><span class="mark ver-used">Active</span><b>v${a.current.n}</b></span>` : ""}
      ${lib && assetEntry(x.id) ? `<button type="button" class="small secondary manage-entry" title="Rename, describe, build method, duplicate (Library › Asset review)">Manage entry…</button>` : ""}
      <span class="hint">${places} · ${d.width}×${d.depth}×${d.height} m${x.kit ? ` · kit ${esc(x.kit)}` : ""}</span>
      <span class="actions"><span class="usage" role="group" aria-label="Usage">${USAGES.map(u =>
        `<button type="button" data-u="${u}" aria-pressed="${x.usage === u}" title="${USAGE_TIPS[u]}">${u}</button>`).join("")}</span>
        ${a.procedural ? `<button type="button" class="${a.reroll ? "secondary " : ""}brief-build" data-needs="vlm blender">${noForm ? "Choose form…" : "Brief & build"}</button>` : ""}
        ${a.reroll ? `<button type="button" class="secondary reroll" aria-expanded="false" title="Generate a new procedural model for this asset. Add guidance to steer it. Seeds only apply to TRELLIS builds.">Reroll</button>` : ""}
        ${a.procedural ? "" : `<label class="new-seed" title="Draw a random seed for Make 3D instead of 42 (the seed is recorded with the result). Without it, Make 3D from the same view and mode repeats the same version."><input type="checkbox" class="new-seed-box"> New seed</label>`}
        <button type="button" class="make3d" data-needs="trellis">Make 3D</button></span>
    </div>
    <div class="review-row">
      ${lib ? `<span class="mark ${a.review === "approved" ? "m-ok" : a.review ? "m-chg" : "m-none"}" title="This asset's review status (also in Library › Asset review).">${esc((a.review || "not reviewed").replace("_", " "))}</span>
        <button type="button" class="secondary approve" ${a.results.length ? `title="${APPROVE_TIP}"` : "disabled title=\"Make 3D first.\""}>Approve</button>` : ""}
      <button type="button" class="secondary recut">Re-cut views…</button>
      ${lib ? `<button type="button" class="secondary changes" title="Mark this asset as needing another build; add the note in Library › Asset review.">Needs changes</button>` : ""}
    </div>
    ${a.reroll ? `<div class="reroll-form row" hidden><input type="text" class="reroll-guidance" maxlength="500"
        placeholder="Guidance (optional): taller windows, an open arcade…" aria-label="Guidance for the reroll">
      <button type="button" class="small default reroll-go" title="Start the reroll; the new version appears under Versions.">Go</button>
      <button type="button" class="small reroll-cancel" title="Close without rerolling.">Cancel</button>
      <div class="hint reroll-settings" style="margin:0;flex-basis:100%"></div></div>` : ""}`;
  const note = rowNote(a);
  if (note) {
    const n = document.createElement("div"); n.className = `row-note ${note.cls}`; n.setAttribute("role", "status");
    n.title = note.title;
    n.innerHTML = `<span>${note.html}</span><button type="button" class="wclose" aria-label="Dismiss this note">x</button>`;
    $(".wclose", n).onclick = () => {
      if (note.key === "dup") delete make3dNotes[`${a.plan}/${x.id}`]; else rowNotesClosed.add(note.key);
      renderReview();
    };
    el.append(n);
  }
  const me = $(".manage-entry", el);
  if (me) me.onclick = () => manageEntry(assetEntry(x.id));
  for (const [cls, status] of [[".approve", "approved"], [".changes", "needs_changes"]]) {
    const b = $(cls, el);
    if (b) b.onclick = async () => {
      try { await api(`/api/projects/${slug}/library/asset/${x.id}/review`, {status, note: ""}); await load(); }
      catch (err) { alert(errorText(err)); }
    };
  }
  el.querySelectorAll(".usage button").forEach(b => b.onclick = async () => {
    try { await api(`/api/projects/${slug}/review/usage`, {plan: a.plan, asset: x.id, usage: b.dataset.u}); await load(); }
    catch (err) { alert(errorText(err)); }
  });

  const sheets = document.createElement("fieldset"); sheets.className = "cut-sheets";
  sheets.innerHTML = `<legend>Cut Sheets</legend>`;
  const g = document.createElement("div"); g.className = "grid views";
  if (!a.views.length) g.innerHTML = `<span class="hint no-views" title="Star a sheet in Library › Reference sheets, then click Extract reference views at the top of this tab.">No views yet: star a sheet, then Extract reference views</span>`;
  const viewName = v => `${v.sheet.split("/").pop().replace(".png", "").replace("sheet_", "s")} v${v.position}`;
  for (const v of a.views) {
    const cell = document.createElement("span"); cell.className = "view-cell"; cell.dataset.key = v.key;
    const b = document.createElement("button");
    b.type = "button"; b.className = "view" + (v.key === a.chosen ? " chosen" : "");
    b.setAttribute("aria-pressed", v.key === a.chosen);
    b.title = `${v.key === a.chosen ? "Make 3D builds from this view; click to unchoose it." : "Click to build from this view with Make 3D."} ${v.sheet}, view ${v.position}${v.asked ? ` (asked: ${v.asked})` : ""}`;
    b.innerHTML = `<img loading="lazy" alt="" src="${fileUrl(v.key)}">
      <span class="label">${v.key === a.chosen ? "✓ " : ""}${v.odd ? "⚠ " : ""}${esc(viewName(v))}</span>`;
    if (v.odd) { b.classList.add("odd"); b.title += ` ${v.odd}`; }
    b.onclick = async () => {
      try { await api(`/api/projects/${slug}/review/choose`, {plan: a.plan, asset: x.id, view: v.key === a.chosen ? null : v.key}); await load(); }
      catch (err) { alert(errorText(err)); }
    };
    // delete: to the trash, asked first; the chosen view stays until another is picked
    const del = document.createElement("button"); del.type = "button"; del.className = "wclose view-del";
    del.textContent = "x"; del.setAttribute("aria-label", `Delete view ${viewName(v)}`);
    del.title = v.key === a.chosen ? "This is the chosen view: pick another view first, then delete it." : "Delete this view (it moves to the project's trash).";
    del.onclick = async ev => {
      ev.stopPropagation();
      if (v.key === a.chosen) { alert("This is the chosen view: pick another view first (or click it to unchoose it), then delete it."); return; }
      if (!(await askDialog("Delete view", `Delete view ${viewName(v)} of ${assetName(x.id, x.name)}? It moves to the project's trash folder.`, "Delete"))) return;
      try { await api(`/api/projects/${slug}/views/delete`, {key: v.key}); await load(); }
      catch (err) { alert(errorText(err)); }
    };
    cell.append(b, del);
    g.append(cell);
  }
  sheets.append(g);
  const odd = a.views.filter(v => v.odd);
  if (odd.length) {
    const n = document.createElement("p"); n.className = "hint odd-views";
    n.textContent = odd.map(v => `${viewName(v)}: ${v.odd}`).join(" ");
    sheets.append(n);
  }
  el.append(sheets);

  const jobs3d = activeJobs("3d.trellis", {plan: a.plan, asset: x.id});
  const make = $(".make3d", el);
  make.disabled = a.procedural || !a.chosen || !!jobs3d.length;
  make.title = a.procedural ? "This entry's build method is procedural: Brief & build or Reroll make its versions. To use Make 3D (TRELLIS), set Build method to TRELLIS in Manage entry…."
    : a.chosen ? "Build a 3D model with TRELLIS from the chosen view; it becomes a new version." : "Choose a reference view first.";
  if (jobs3d.length) {
    make.textContent = jobs3d[0].status === "queued" ? "Queued…" : "Building…";
    make.after(stopButton(jobs3d));
  }
  make.onclick = async () => {
    const k = `${a.plan}/${x.id}`;
    delete make3dNotes[k];
    try {
      const r = await api(`/api/projects/${slug}/3d`, {plan: a.plan, asset: x.id, mode: $("#trellis-mode").value, new_seed: !!$(".new-seed-box", el)?.checked});
      if (r && r.duplicate != null) { make3dNotes[k] = {message: r.message, why: r.why}; renderReview(); }
      else await load();
    }
    catch (err) { alert(errorText(err)); }
  };

  // procedural: Brief & build from the newest starred sheet (its own, as Library › Reference sheets
  // uses); no form yet: Choose form… opens the same dialog with the Form field focused
  const bb = $(".brief-build", el);
  if (bb) {
    const own = a.starred.filter(s => s.sheet.split("/")[2] === x.id), sheet = (own.length ? own : a.starred).map(s => s.sheet).at(-1);
    const bbJobs = activeJobs("library.brief_build", {asset: x.id});
    bb.disabled = !sheet || !nx || !!bbJobs.length;
    bb.title = !sheet ? `Star a sheet of this asset in Library › Reference sheets first${noForm ? " (or set the form in Manage entry…)" : ""}.`
      : noForm ? `No building form yet: choose it (and the size), then brief from ${sheet.split("/").pop()} and build.`
      : `Brief from ${sheet.split("/").pop()} with the vision model, then build in Blender (a new version).`;
    if (bbJobs.length) { bb.textContent = bbJobs[0].status === "queued" ? "Queued…" : "Building…"; bb.after(stopButton(bbJobs)); }
    bb.onclick = () => refsBriefBuild({key: x.id, title: assetName(x.id, x.name), sheets: a.starred.map(s => s.sheet)}, sheet, nx,
      noForm ? {focus: "form"} : null);
  }

  const rc = $(".recut", el), rcJobs = activeJobs("views.cut", {asset: x.id});
  rc.disabled = !a.starred.length || !!rcJobs.length;
  rc.title = a.starred.length ? "Cut this asset's views again from its starred sheet; the old ones move to the trash."
    : "Star a sheet of this asset in Library › Reference sheets first.";
  if (rcJobs.length) { rc.textContent = rcJobs[0].status === "queued" ? "Queued…" : "Cutting…"; rc.after(stopButton(rcJobs)); }
  rc.onclick = () => recutDialog(a);

  const rr = $(".reroll", el);
  if (rr) {
    const form = $(".reroll-form", el), field = $(".reroll-guidance", el);
    const rrJobs = activeJobs("library.reroll", {asset: x.id});
    rr.disabled = !!(rrJobs.length || ["library.rebuild", "library.brief_build"].some(k => activeJobs(k, {asset: x.id}).length));
    if (rrJobs.length) { rr.textContent = rrJobs[0].status === "queued" ? "Queued…" : "Rerolling…"; rr.after(stopButton(rrJobs)); }
    const open = on => { form.hidden = !on; rr.setAttribute("aria-expanded", on); if (on) { field.focus(); rerollSettings(x.id, form); } };
    rr.onclick = () => open(form.hidden);
    $(".reroll-cancel", el).onclick = () => open(false);
    const go = async () => {
      const guidance = field.value.trim();
      try { await api(`/api/projects/${slug}/library/asset/${encodeURIComponent(x.id)}/reroll`, {guidance}); open(false); field.value = ""; await load(); }
      catch (err) { alert(errorText(err)); }
    };
    $(".reroll-go", el).onclick = go;
    field.onkeydown = ev => { if (ev.key === "Enter") { ev.preventDefault(); go(); } else if (ev.key === "Escape") open(false); };
  }

  // fixed layout: the version strip in its own column (reserved even before it loads), the model
  // cards beside it; nothing wraps differently from one poll to the next
  const body = document.createElement("div"); body.className = "ar-body" + (a.plan === "library" ? " with-strip" : "");
  el.append(body);
  if (a.plan === "library") {
    const vs = document.createElement("fieldset"); vs.className = "version-strip"; body.append(vs);
    if (a.results.length || a.current) versionStrip(vs, x.id, () => load());
    else vs.innerHTML = `<legend>Versions</legend><span class="hint">No builds yet: ${a.procedural ? `${noForm ? "Choose form…, then " : ""}Brief & build${a.reroll ? " or Reroll" : ""}` : "Make 3D"}.</span>`;
  }
  const models = document.createElement("div"); models.className = "ar-models"; body.append(models);
  if (a.results.length) {
    const r = document.createElement("div"); r.className = "results";
    for (const res of a.results) {
      const c = document.createElement("div"); c.className = "result";
      if (res.current) c.classList.add("current");
      c.dataset.n = res.n ?? "";
      c.innerHTML = `<model-viewer src="${fileUrl(res.glb)}" camera-controls auto-rotate ${MV_LOOK}
          alt="3D model of ${esc(a.plan === "library" ? assetName(x.id, x.name) : x.name)}" loading="lazy"></model-viewer>
        <span>${res.n != null ? `<b>v${res.n}</b> ${esc(res.kind === "trellis" ? "TRELLIS" : res.kind)}` : esc(res.name)}${res.seed != null ? ` · seed ${res.seed}` : ""}
          ${res.current && !(res.n != null && lib) ? `<span class="mark ver-used" title="${ACTIVE_TIP}">Active</span>` : ""}</span>
        <span class="hint">${esc(modelStats(res))}</span>
        ${res.n != null && lib ? `<span class="card-btns">${res.current ? `<span class="mark ver-used" title="${ACTIVE_TIP}">Active</span>`
          : `<button type="button" class="small ver-restore" title="Make this the Active version that Cleanup and Unreal look scenes use.">Use this version</button>`}
          <button type="button" class="small ver-delete"${res.current ? ` disabled title="The Active version can't be deleted: use another version first."`
            : res.approved ? ` disabled title="The Approved version can't be deleted."` : ` title="Delete this version and its files (asks first)."`}>Delete</button></span>` : ""}
        <span><a href="${fileUrl(res.glb)}" download title="Download this version's 3D model (GLB).">GLB</a>${res.input ? ` · <a href="${fileUrl(res.input)}" target="_blank" title="The image TRELLIS built this model from.">Image sourced</a>` : ""}</span>
        ${lib ? "" : `<span><button type="button" class="small card-cleanup">Clean up</button></span>`}`;
      // the version actions live on the cards (the strip holds the cleanup steps); versions.js does the work
      const ver = () => ((VER_CACHE[`${slug}/${x.id}`] || {}).versions || []).find(v => v.n === res.n) || res;
      const use = $(".ver-restore", c);
      if (use) use.onclick = () => versionRestore(x.id, ver(), () => load());
      const del = $(".ver-delete", c);
      if (del) del.onclick = () => versionDelete(x.id, ver(), () => versionsChanged(x.id, () => load()));
      const cl = $(".card-cleanup", c);
      if (cl) cleanupButton(cl, a.plan, x.id, res.glb, x.usage);
      r.append(c);
    }
    models.append(r);
  }
  if (a.cleanups.length) {
    // library: each card says which version it was cleaned from; other versions' cleanups wait
    // behind a toggle, so the one beside the Active version is always its own
    const key = `${a.plan}/${x.id}`, lib = a.plan === "library";
    const old = lib ? a.cleanups.filter(c => !c.active) : [];
    const r = document.createElement("div"); r.className = "results" + (OLD_CLEANUPS.has(key) ? " show-old" : "");
    for (const c of a.cleanups) {
      const d = c.output_dims_m.map(v => v.toFixed(2)).join(" × ");
      const box = document.createElement("div"); box.className = "result" + (lib && !c.active ? " old-cleanup" : "");
      box.innerHTML = `<model-viewer src="${fileUrl(c.glb)}" camera-controls ${MV_LOOK}
          alt="Cleaned model of ${esc(a.plan === "library" ? assetName(x.id, x.name) : x.name)}" loading="lazy"></model-viewer>
        <span>Cleaned${lib ? (c.version ? ` from v${c.version}` : " from a removed version") : ""} (${esc(c.usage)}${c.retopo_method ? `, ${esc(c.retopo_method)}` : ""})${c.active ? ` <span class="mark ver-used" title="Cleaned from the Active version: what Unreal places for this tier.">Active</span>` : ""}</span>
        <span class="hint">${(c.output_faces / 1000).toFixed(c.output_faces < 10000 ? 1 : 0)}k faces · ${d} m (plan ${x.dimensions.width} × ${x.dimensions.depth} × ${x.dimensions.height})</span>
        ${c.warnings.length ? `<span class="error">${c.warnings.map(esc).join("; ")}</span>` : ""}
        <span><a href="${fileUrl(c.glb)}" download title="Download the cleaned 3D model (GLB).">GLB</a></span>`;
      r.append(box);
    }
    if (old.length) {
      const t = document.createElement("label"); t.className = "check old-cleanups-toggle";
      t.innerHTML = `<input type="checkbox" ${OLD_CLEANUPS.has(key) ? "checked" : ""}> Show older cleanups (${old.length})`;
      t.title = "Clean up outputs of versions that aren't Active (Unreal doesn't place them).";
      $("input", t).onchange = ev => { ev.target.checked ? OLD_CLEANUPS.add(key) : OLD_CLEANUPS.delete(key); r.classList.toggle("show-old", ev.target.checked); };
      models.append(t);
    }
    models.append(r);
  }
  if (!a.results.length && !a.cleanups.length) models.innerHTML = `<span class="hint">No 3D model yet.</span>`;
  return el;
}

// Clean up of a scene-mode 3D card (library versions have it on their Versions row): scale to the plan size,
// pivot at the base, and for game use decimate and bake. glb: the model, project-relative.
function cleanupButton(b, plan, asset, glb, usage) {
  const jobs = activeJobs("cleanup", {plan, asset, glb});
  b.disabled = !!jobs.length;
  b.title = `Scale this model to the plan size and set its pivot at the base${usage === "game" ? ", then decimate and bake it for game use" : ""}.`;
  if (jobs.length) { b.textContent = jobs[0].status === "queued" ? "Queued…" : "Cleaning…"; b.after(stopButton(jobs)); }
  b.onclick = async () => {
    b.disabled = true;
    try { await api(`/api/projects/${slug}/cleanup`, {plan, asset, glb}); await load(); }
    catch (err) { alert(errorText(err)); b.disabled = false; }
  };
}

// Re-cut views…: one of the asset's starred sheets (the newest by default) cut again, its old views to
// the trash; or the whole sheet as one view, for one object drawn across the sheet
function recutDialog(a) {
  const x = a.asset, short = k => k.split("/").pop(), newest = a.starred.at(-1);
  const body = document.createElement("div"); body.id = "recut-body";
  body.innerHTML = `<p>Cut the views of <b>${esc(assetName(x.id, x.name))}</b> again from its starred sheet. The views cut from that sheet now
      move to the project's trash folder${a.views.some(v => v.key === a.chosen) ? " (a chosen one among them is unchosen)" : ""}.</p>
    <p>${a.starred.length > 1 ? `<label>Sheet: <select id="recut-sheet">${a.starred.map(s =>
      `<option value="${esc(s.sheet)}"${s === newest ? " selected" : ""}>${esc(short(s.sheet))}${s.kind === "kit" ? " (kit sheet)" : ""}</option>`).join("")}</select></label>`
      : `Sheet: <code>${esc(short(newest.sheet))}</code>`}</p>
    <label class="check"><input type="checkbox" id="recut-whole"> Cut the whole sheet as one view</label>
    <p class="hint">For an asset drawn as one object across the sheet (a long stepped quay, say) instead of three separate views.</p>`;
  const sel = $("#recut-sheet", body), whole = $("#recut-whole", body);
  const sync = () => {
    const kit = a.starred.find(s => s.sheet === (sel ? sel.value : newest.sheet)).kind === "kit";
    whole.disabled = kit; if (kit) whole.checked = false;
    whole.parentElement.title = kit ? "A kit sheet holds several pieces: it can't be one view." : "";
  };
  if (sel) sel.onchange = sync;
  sync();
  dialog({title: "Re-cut views", body, buttons: [
    {label: "Re-cut", default: true, onClick: async () => {
      try { await api(`/api/projects/${slug}/views/recut`, {plan: a.plan, asset: x.id, sheet: sel ? sel.value : newest.sheet, whole: whole.checked}); await load(); }
      catch (err) { alert(errorText(err)); return false; }
    }},
    {label: "Cancel"}]}).win.id = "recut-dialog";
}

$("#cut-views").onclick = async () => {
  try { await api(`/api/projects/${slug}/views/cut`, {}); await load(); }
  catch (err) { alert(errorText(err)); }
};


// Reroll's guidance box: the settings this asset's builder has, in a gray line and the placeholder
async function rerollSettings(asset, form) {
  const line = $(".reroll-settings", form), field = $(".reroll-guidance", form);
  if (line.dataset.loaded === asset) return;
  try {
    const st = await api(`/api/projects/${slug}/library/asset/${encodeURIComponent(asset)}/settings`, undefined, "GET");
    line.textContent = `Settings this builder has: ${st.labels.join(", ")}.`;
    field.placeholder = `Guidance (optional): ${(st.examples || st.labels.slice(0, 4)).join(", ")}…`;
    line.dataset.loaded = asset;
  } catch (err) { line.textContent = ""; }
}
