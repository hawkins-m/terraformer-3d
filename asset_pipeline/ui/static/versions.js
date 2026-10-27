// Asset version bank (server side: asset_pipeline/versions.py, ui/versions_api.py).
// - versionStrip(box, asset, onChange, opts): the box of an asset's builds (Views and 3D, and
//   Library › Asset review's detail view). Per version: Compare tick, thumbnail, vN, method, date,
//   source (sheet, view, guidance, seed), size, Active badge, and the cleanup steps: the ones not
//   done yet as buttons (Clean up, Smooth shading, Remove ground), done ones as grey ticks; a step
//   that doesn't apply (a procedural version has no Smooth or Remove ground) shows neither.
//   Views and 3D puts Use this version / Delete on its 3D model cards; opts.manage (Asset review,
//   which has no cards) puts them on the rows. Two ticked = Compare, at the bottom of the box.
//   There is no Keep any more: retention never removes the Active or an Approved version, and
//   versions marked Keep in the earlier UI ("pinned") stay protected.
// - Tools › Storage…: what the builds and versions take, per asset and per project, with
//   "Delete old versions" (never the Active or Approved ones).
// - Tools › Settings…: how many recent versions to keep per asset.
// Loaded after tools.js (jobDone, confirmDialog).
Object.assign(JOB_LABELS, {"library.restore": "switching the version in use", "library.assembly": "attached pieces preview"});
Object.assign(LOADERS, {"library.restore": ["Switching the version in use…", "greybox", "steps"], "library.assembly": ["Placing the attached pieces…", "greybox", ""]});
const fmtCount = n => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(n);
// a 3D model's plain stats: "faces 934k · file 36.4 MB · peak GPU memory 9.2 GB"
const modelStats = r => [r.faces ? `faces ${fmtCount(r.faces)}` : "", r.file_bytes ? `file ${fmtBytes(r.file_bytes)}` : "",
  r.peak_vram_gb ? `peak GPU memory ${(+r.peak_vram_gb).toFixed(1)} GB` : ""].filter(Boolean).join(" · ");

const fmtBytes = b => b >= 1e9 ? `${(b / 1e9).toFixed(2)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : b >= 1e3 ? `${Math.round(b / 1e3)} KB` : `${b || 0} B`;
const verDate = s => (s || "").replace("T", " ").slice(0, 16);
const verBase = p => String(p || "").split("/").pop();
const verSource = v => {
  const s = v.source || {}, bits = [];
  if (s.sheet) bits.push(verBase(s.sheet).replace(".png", "") + (s.position != null && s.position !== "" ? ` v${s.position}` : ""));
  if (s.form) bits.push(s.form);
  if (s.seed != null) bits.push(`seed ${s.seed}`);
  if (s.mode) bits.push(s.mode);
  if (s.guidance) bits.push(`“${s.guidance.length > 60 ? s.guidance.slice(0, 57) + "…" : s.guidance}”`);
  return bits.join(" · ");
};

// Beside Approve: which version export and Unreal take. Cleanup takes the version in use; Unreal
// look scenes place only Approved builds; Export sends greybox stand-ins (s7_export: real assets
// are not exported yet). cur: {n, kind} or null.
function versionUseLine(cur, approved) {
  if (!cur) return "No version in use yet: Unreal look scenes keep the greybox here. Export always sends greybox stand-ins.";
  if (cur.kind === "trellis")       // look scenes place TRELLIS models only as pieces attached to a placed building
    return `Cleanup uses v${cur.n} (TRELLIS), the version marked “Active”; Unreal look scenes place it only as a piece ` +
      "attached to an Approved building. Export sends greybox stand-ins.";
  return `Cleanup and Unreal look scenes use v${cur.n} (${cur.kind}), the version marked “Active”` +
    `${approved ? "" : "; look scenes place it only once it is Approved"}. Export sends greybox stand-ins.`;
}

async function versionJob(job) {        // a started job: wait (api() has opened its progress dialog), then say if it failed
  const j = await jobDone(job);
  if (j.status === "error") alert(errorText(j.error));
  return j;
}

// box: the element, or a selector looked up again after the request (the view may have been
// re-rendered meanwhile, leaving the first element detached and the strip empty)
// the last strip data per asset: a rebuilt row draws it at once instead of an empty box
const VER_CACHE = {};
async function versionStrip(boxOrSel, asset, onChange, opts = {}) {
  const find = () => typeof boxOrSel === "string" ? document.querySelector(boxOrSel) : boxOrSel;
  if (!find()) return;
  find()._opts = opts;
  const ck = `${slug}/${asset}`, cached = VER_CACHE[ck];
  if (cached) versionStripDraw(find(), asset, cached, onChange);
  let d, err = null;
  try { d = await api(`/api/projects/${slug}/library/asset/${asset}/versions`, undefined, "GET"); } catch (e) { err = e; }
  const box = find();
  if (!box || !box.isConnected) return;
  if (err) { box.innerHTML = `<span class="error">${esc(errorText(err))}</span>`; return; }
  VER_CACHE[ck] = d;
  if (cached && box.dataset.drawn === "1" && JSON.stringify(cached) === JSON.stringify(d)) return;   // nothing new: no redraw
  versionStripDraw(box, asset, d, onChange);
}

// One refresh for every version action (Delete, Use this version, Remove old versions, a cleanup
// step, and a finished Make 3D / Reroll / rebuild / restore / Clean up): fetch the asset's versions, mark
// its rows changed (ROW_REV, in the Views and 3D row's sig), reload the view, then redraw every
// strip of the asset on the page. The bug: Views and 3D rows are keyed on /review data, which
// deleting a version without a 3D model doesn't change, so the row and its strip stayed stale.
const ROW_REV = {};
const verRev = asset => ROW_REV[`${slug}/${asset}`] || 0;
async function versionsChanged(asset, onChange) {
  const ck = `${slug}/${asset}`;
  try { VER_CACHE[ck] = await api(`/api/projects/${slug}/library/asset/${asset}/versions`, undefined, "GET"); }
  catch (err) { delete VER_CACHE[ck]; }
  ROW_REV[ck] = verRev(asset) + 1;
  if (onChange) { try { await onChange(); } catch (err) { alert(errorText(err)); } }
  const d = VER_CACHE[ck];
  if (d) document.querySelectorAll(".version-strip").forEach(box => {
    if (box.dataset.asset === asset && box.isConnected) versionStripDraw(box, asset, d, box._onChange);
  });
}

// load(): a job that makes or switches versions has finished since the last load -> refresh its asset
const VERSION_JOBS = new Set(["3d.trellis", "library.reroll", "library.restore", "library.rebuild", "library.brief_build", "library.smooth", "library.ground", "cleanup"]);
let verJobsSeen = null;
async function versionsAfterLoad() {
  const done = data.jobs.filter(j => VERSION_JOBS.has(j.kind) && j.tag && j.tag.asset && (j.tag.plan || "library") === "library" && !isActive(j));
  if (!verJobsSeen || verJobsSeen.slug !== slug) { verJobsSeen = {slug, ids: new Set(done.map(j => j.id))}; return; }
  const assets = [...new Set(done.filter(j => !verJobsSeen.ids.has(j.id)).map(j => j.tag.asset))];
  done.forEach(j => verJobsSeen.ids.add(j.id));
  await Promise.all(assets.map(a => versionsChanged(a)));     // load() renders the rows next
}

// a version's cleanup steps: [what, done, button class, button label, tick text, title]; a step that
// doesn't apply to the version is left out (no button, no tick)
const SMOOTH_TIP = "Weld the model and shade it smooth by angle (30°: curves smooth, sharp edges crisp), as a new version; a few seconds, no regeneration.";
const GROUND_TIP = "Cut off a thin ground slab TRELLIS built under the model from the sheet's shadow or floor, and stand the model on its own base, as a new version; plinths, podiums and steps stay. A few seconds.";
function verSteps(v, usage) {
  const out = [];
  if (v.glb) out.push(["cleanup", !!v.cleaned, "ver-clean", "Clean up", `✓ cleaned${usage ? ` (${usage})` : ""}`,
    v.cleaned ? `Clean up has made this version's ${usage || ""} model (Unreal places it for the Active version).` : ""]);
  if (v.kind === "trellis" && (v.smoothable || v.smoothed))
    out.push(["smooth", !!v.smoothed, "ver-smooth", "Smooth shading", "✓ smoothed", v.smoothed ? "Welded and shaded smooth by angle." : SMOOTH_TIP]);
  if (v.kind === "trellis" && (v.groundable || v.ground_checked))
    out.push(["ground", !!v.ground_checked, "ver-ground-rm", "Remove ground", /^No ground/.test(v.ground || "") ? "✓ no ground found" : "✓ ground removed",
      v.ground_checked ? v.ground || "The ground was looked for." : GROUND_TIP]);
  return out;
}

function versionStripDraw(box, asset, d, onChange) {
  box.dataset.drawn = "1";
  box._onChange = onChange;
  const manage = !!(box._opts && box._opts.manage);
  if (box.dataset.asset !== asset) box.dataset.sel = "[]";
  box.dataset.asset = asset;
  const sel = new Set(JSON.parse(box.dataset.sel || "[]").filter(n => d.versions.some(v => v.n === n)));
  const cleaning = v => v.glb && typeof activeJobs === "function" ? activeJobs("cleanup", {plan: "library", asset, glb: v.glb}) : [];
  const draw = () => {
    box.dataset.sel = JSON.stringify([...sel]);
    box.className = "version-strip";
    box.innerHTML = `<legend title="Keeps your last ${d.keep} versions; the Active and Approved versions are never removed.">Versions ${d.versions.length} <span class="hint" style="margin:0" data-role="total">· ${fmtBytes(d.bytes)}</span></legend>
      ${d.prunable.length ? `<div class="ver-prune row"><span>${d.prunable.length} old version${d.prunable.length === 1 ? "" : "s"} can be removed (${fmtBytes(d.prunable_bytes)})</span>
        <button type="button" class="small ver-prune-btn" title="Delete these old versions (asks first); the Active and Approved versions stay.">Remove</button></div>` : ""}
      <div class="ver-list">${d.versions.map(v => `<div class="ver${v.current ? " cur" : ""}" data-n="${v.n}">
        <label class="check ver-pick" title="Tick two versions to compare them"><input type="checkbox" class="ver-sel" ${sel.has(v.n) ? "checked" : ""} aria-label="Compare v${v.n}"> Compare</label>
        ${v.thumb ? `<img loading="lazy" alt="Thumbnail of v${v.n}" src="${fileUrl(v.thumb)}">` : `<div class="ver-nothumb">no render</div>`}
        <div class="ver-meta"><b>v${v.n}</b> <span class="hint" style="margin:0">${esc(v.kind)} · ${esc(verDate(v.created))}</span>
          <div class="hint ver-src" style="margin:0">${esc(verSource(v)) || "&nbsp;"}</div>
          <div class="hint ver-changes" style="margin:0" title="${esc(v.changes ? `What changed in the brief:\n${v.changes.replace(/^Changed: /, "").replace(/; | · /g, "\n")}` : "")}">${esc(v.changes_short || v.changes || "") || "&nbsp;"}</div>
          <div class="ver-marks"><span class="hint" style="margin:0">${fmtBytes(v.bytes)}${v.has_glb && v.kind === "procedural" ? " · with 3D model" : ""}</span>${v.current ? `<span class="mark ver-used" title="This version is the one later steps (Cleanup, Unreal look scenes) will use.">Active</span>` : ""}${v.approved ? `<span class="mark m-ok" title="The version that was Active when the asset was approved; never removed.">approved</span>` : ""}${v.pinned ? `<span class="mark m-new" title="Marked Keep in an earlier version of the app: never removed.">kept</span>` : ""}</div></div>
        <div class="ver-btns">${verSteps(v, d.usage).map(([k, done, cls, label, tick, tip]) => done
            ? `<span class="ver-done" data-step="${k}" title="${esc(tip)}">${esc(tick)}</span>`
            : `<button type="button" class="small ${cls}" data-step="${k}"${tip ? ` title="${esc(tip)}"` : ""}>${label}</button>`).join("")}
          ${manage ? `${v.current ? "" : `<button type="button" class="small ver-restore" title="Make this the Active version that Cleanup and Unreal look scenes use.">Use this version</button>`}
          <button type="button" class="small ver-delete"${v.approved ? " disabled title=\"The Approved version can't be deleted.\"" : v.current ? " disabled title=\"The Active version can't be deleted: use another version first.\"" : " title=\"Delete this version and its files (asks first).\""}>Delete</button>` : ""}</div></div>`).join("")}</div>
      <div class="row ver-cmp-row"><button type="button" class="small ver-compare" ${sel.size === 2 ? "" : "disabled"} title="Show the two ticked versions side by side with what differs.">Compare</button>
        <span class="hint" style="margin:0">Tick two versions to compare them.</span></div>`;
    box.querySelectorAll(".ver").forEach(row => {
      const n = +row.dataset.n, v = d.versions.find(x => x.n === n);
      $(".ver-sel", row).onchange = ev => {
        if (ev.target.checked) { if (sel.size >= 2) sel.delete([...sel][0]); sel.add(n); } else sel.delete(n);
        draw();
      };
      const use = $(".ver-restore", row);
      if (use) use.onclick = () => versionRestore(asset, v, onChange);
      const del = $(".ver-delete", row);
      if (del) del.onclick = () => versionDelete(asset, v, () => versionsChanged(asset, onChange));
      const cl = $(".ver-clean", row), cj = cleaning(v);
      if (cl && cj.length) { cl.disabled = true; cl.textContent = cj[0].status === "queued" ? "Queued…" : "Cleaning…"; }
      // Clean up of this version's model; the job's end refreshes the strip (VERSION_JOBS)
      if (cl) cl.onclick = async () => {
        cl.disabled = true;
        try { await api(`/api/projects/${slug}/cleanup`, {plan: "library", asset, glb: v.glb}); if (typeof load === "function") await load(); }
        catch (err) { alert(errorText(err)); cl.disabled = false; }
      };
      const sm = $(".ver-smooth", row);
      if (sm) sm.onclick = async () => {
        sm.disabled = true;
        try { await jobDone(await api(`/api/projects/${slug}/library/asset/${asset}/versions/${n}/smooth`, {})); }
        catch (err) { alert(errorText(err)); }
        await versionsChanged(asset, onChange);
      };
      const gr = $(".ver-ground-rm", row);
      if (gr) gr.onclick = async () => {
        gr.disabled = true;
        try { await jobDone(await api(`/api/projects/${slug}/library/asset/${asset}/versions/${n}/ground`, {})); }
        catch (err) { alert(errorText(err)); }
        await versionsChanged(asset, onChange);
      };
    });
    const cmp = $(".ver-compare", box);
    if (cmp) cmp.onclick = () => versionCompare(asset, ...[...sel].sort((a, b) => a - b));
    const pr = $(".ver-prune-btn", box);
    if (pr) pr.onclick = async () => {
      const ok = await confirmDialog({title: "Remove old versions", button: "Remove", danger: true, id: "version-prune-dialog",
        body: `<p>Remove ${d.prunable.length} old version${d.prunable.length === 1 ? "" : "s"} of <b>${esc(assetName(asset))}</b>
          (${d.prunable.map(n => "v" + n).join(", ")}), freeing ${fmtBytes(d.prunable_bytes)}?</p>
          <p class="hint">The last ${d.keep}, the Active and the Approved versions stay.</p>`});
      if (!ok) return;
      try { await api(`/api/projects/${slug}/library/asset/${asset}/versions/prune`, {dry_run: false}); }
      catch (err) { alert(errorText(err)); }
      await versionsChanged(asset, onChange);
    };
  };
  draw();
}

async function versionRestore(asset, v, onChange) {
  const route = v.kind === "trellis" ? "TRELLIS" : "procedural";
  const body = (v.kind === "trellis"
    ? `<p>Use v${v.n} (${esc(verSource(v))}) as the 3D model of <b>${esc(assetName(asset))}</b> that cleanup and export take. Nothing is deleted.</p>`
    : `<p>Use v${v.n} for <b>${esc(assetName(asset))}</b>: its brief goes back${v.has_glb ? " with its saved 3D model as the build." : " and the asset is rebuilt (a new version)."}
       Unsaved edits to the brief are lost.</p>`)
    + `<p class="hint">The entry's build method switches to ${route} if it isn't already.</p>`;
  if (!await confirmDialog({title: `Use v${v.n}`, button: "Use this version", id: "version-restore-dialog", body})) return;
  let j = null;
  try { const job = await api(`/api/projects/${slug}/library/asset/${asset}/versions/${v.n}/restore`, {}); j = await versionJob(job); }
  catch (err) { alert(errorText(err)); }
  await versionsChanged(asset, onChange);
  const r = j && j.status === "done" && j.result;
  if (r) {
    const done = document.createElement("div"); done.id = "version-used-body";
    done.innerHTML = `<p><b>v${r.current}</b> of <b>${esc(assetName(asset))}</b> is now used by the pipeline${r.rebuilt ? ` (rebuilt from v${r.from})` : ""}.</p>
      ${r.route ? `<p>Build method switched from ${esc(r.route.from || "auto")} to ${esc(r.route.to === "trellis" ? "TRELLIS" : r.route.to)}.</p>` : ""}
      ${r.glb ? `<p class="hint">Cleanup and export take <code>${esc(r.glb)}</code>.</p>` : ""}`;
    dialog({title: "Version in use", body: done, buttons: [{label: "OK", default: true}]}).win.id = "version-used-dialog";
  }
}

async function versionDelete(asset, v, after) {
  const url = `/api/projects/${slug}/library/asset/${asset}/versions/${v.n}`;
  const ok = await confirmDialog({title: `Delete v${v.n}`, button: "Delete", danger: true, id: "version-delete-dialog",
    body: `<p>Delete v${v.n} of <b>${esc(assetName(asset))}</b> (${fmtBytes(v.bytes)})?${v.kind === "trellis" ? " Its 3D model file is removed too." : ""}</p>
      ${v.pinned ? `<p class="error">It was marked Keep in an earlier version of the app. Deleting it removes it and its saved files.</p>` : ""}`});
  if (!ok) return;
  try { await api(url + (v.pinned ? "?confirm_pinned=true" : ""), {}, "DELETE"); }
  catch (err) { alert(errorText(err)); }
  await after();
}

async function versionCompare(asset, a, b) {
  let c;
  try { c = await api(`/api/projects/${slug}/library/asset/${asset}/versions/compare?a=${a}&b=${b}`, undefined, "GET"); }
  catch (err) { alert(errorText(err)); return; }
  const val = x => x == null || x === "" ? "–" : typeof x === "object" ? JSON.stringify(x) : String(x);
  const col = (v, key) => `<figure class="ver-cmp-col" data-side="${key}"><b>v${v.n}</b> <span class="hint" style="margin:0">${esc(v.kind)} · ${esc(verDate(v.created))} · ${fmtBytes(v.bytes)}</span>
    ${v.thumb ? `<img alt="Thumbnail of v${v.n}" src="${fileUrl(v.thumb)}">` : `<div class="ver-nothumb">no render</div>`}
    <div class="hint">${esc(verSource(v))}</div></figure>`;
  const table = (rows, k1, k2, head) => rows.length ? `<table class="grid-table"><thead><tr><th>${head}</th><th>v${c.a.n}</th><th>v${c.b.n}</th></tr></thead><tbody>
    ${rows.map(r => `<tr><td>${esc(r[k1])}</td><td>${esc(val(r.a))}</td><td>${esc(val(r.b))}</td></tr>`).join("")}</tbody></table>` : "";
  const body = document.createElement("div"); body.id = "version-compare-body";
  body.innerHTML = `<div class="ver-cmp">${col(c.a, "a")}${col(c.b, "b")}</div>
    <h3>Source</h3>${table(c.source_diff, "field", "", "Field") || `<p class="hint">Same source.</p>`}
    ${c.a.kind === "procedural" && c.b.kind === "procedural" ? `<h3>Brief</h3>${table(c.brief_diff, "path", "", "Value") || `<p class="hint">Same brief.</p>`}` : ""}`;
  const dlg = dialog({title: `Compare v${a} and v${b}`, body, className: "tool-window", buttons: [{label: "Close", default: true}]});
  dlg.win.id = "version-compare-dialog";
}

// --- Tools › Storage… -------------------------------------------------------------------
async function storageDialog() {
  if (noProject()) return;
  const body = document.createElement("div"); body.id = "storage-body";
  const dlg = dialog({title: "Storage", body, className: "tool-window", buttons: [{label: "Close", default: true}]});
  dlg.win.id = "storage-dialog";
  const draw = async () => {
    let s;
    try { s = await api(`/api/projects/${slug}/storage`, undefined, "GET"); }
    catch (err) { body.innerHTML = `<span class="error">${esc(errorText(err))}</span>`; return; }
    const f = s.files || {};
    body.innerHTML = `<p id="st-total"><b>${fmtBytes(s.total)}</b> in asset builds and versions: ${fmtBytes(s.current_bytes)} current build files,
        ${fmtBytes(s.versions_bytes)} in versions. The last ${s.keep} versions of each asset stay (Tools › Settings…), and the Active and Approved ones always.</p>
      <fieldset id="st-shared"><legend>Shared file store</legend>
        <p style="margin:0 0 4px">Sheets, cut views and 3D models are stored once and referenced by every entry and version that uses them:
          <b>${fmtBytes(f.bytes || 0)}</b> on disk for ${(f.references || 0).toLocaleString("en")} references to ${(f.objects || 0).toLocaleString("en")} files,
          <b id="st-saved">${fmtBytes(f.saved || 0)} saved</b> by sharing (copies would take ${fmtBytes(f.referenced_bytes || 0)}).</p>
        <div class="row"><button type="button" id="st-clean"${f.unreferenced ? "" : " disabled"}>Clean unreferenced files (${f.unreferenced || 0}, ${fmtBytes(f.unreferenced_bytes || 0)})</button>
          <span class="hint" style="margin:0">Stored files no sheet, view, build or version refers to any more.</span></div></fieldset>
      <fieldset id="st-loose"><legend>Builds that aren't versions</legend>
        ${(s.loose || []).length ? `<ul style="margin:0 0 4px">${s.loose.map(r => `<li>${assetLabelHtml(r.asset)}: <code>${esc(verBase(r.file))}</code> (${fmtBytes(r.bytes)})</li>`).join("")}</ul>
          <button type="button" id="st-loose-rm">Remove these files (${s.loose.length}, ${fmtBytes(s.loose_bytes)})</button>`
          : `<p class="hint" style="margin:0">None: every 3D model on disk belongs to a version (older TRELLIS results are taken into versions).</p>`}</fieldset>
      ${s.assets.length ? `<table class="grid-table"><thead><tr><th>Asset</th><th>Versions</th><th>Versions size</th><th>Build files</th><th>Total</th><th></th></tr></thead><tbody>
        ${s.assets.map(r => `<tr data-asset="${esc(r.asset)}"><td>${assetLabelHtml(r.asset)}</td><td>${r.versions}</td>
          <td>${fmtBytes(r.versions_bytes)}</td><td>${fmtBytes(r.current_bytes)}</td><td>${fmtBytes(r.total)}</td>
          <td><button type="button" class="small st-prune-asset"${r.removable ? "" : " disabled"}>Delete old versions (${r.removable}, ${fmtBytes(r.removable_bytes)})</button></td></tr>`).join("")}
        </tbody></table>` : `<p class="hint">No asset builds yet.</p>`}
      <div class="row"><button type="button" id="st-prune-all"${s.removable ? "" : " disabled"}>Delete old versions in the project (${s.removable}, ${fmtBytes(s.removable_bytes)})</button>
        <span class="hint" style="margin:0">The Active and Approved versions are never deleted.</span></div>`;
    const prune = async (asset, n, bytes) => {
      const ok = await confirmDialog({title: "Delete old versions", button: "Delete", danger: true, id: "storage-confirm-dialog",
        body: `<p>Delete ${n} version${n === 1 ? "" : "s"} ${asset ? `of <b>${esc(assetName(asset))}</b>` : "in the whole project"} other than the Active and Approved ones, freeing ${fmtBytes(bytes)}?</p>
          <p class="hint">The Active and Approved versions stay. This cannot be undone.</p>`});
      if (!ok) return;
      try { await api(`/api/projects/${slug}/storage/prune`, asset ? {asset} : {}); }
      catch (err) { alert(errorText(err)); }
      draw();
    };
    body.querySelectorAll("tr[data-asset]").forEach(tr => {
      const r = s.assets.find(x => x.asset === tr.dataset.asset);
      $(".st-prune-asset", tr).onclick = () => prune(r.asset, r.removable, r.removable_bytes);
    });
    $("#st-prune-all", body).onclick = () => prune(null, s.removable, s.removable_bytes);
    const lrm = $("#st-loose-rm", body);
    if (lrm) lrm.onclick = async () => {
      const ok = await confirmDialog({title: "Remove builds that aren't versions", button: "Remove", danger: true, id: "storage-loose-dialog",
        body: `<p>Remove ${s.loose.length} 3D model file${s.loose.length === 1 ? "" : "s"} no version lists, freeing up to ${fmtBytes(s.loose_bytes)}?</p>`});
      if (!ok) return;
      try { await api(`/api/projects/${slug}/storage/loose`, {}); } catch (err) { alert(errorText(err)); }
      draw();
    };
    $("#st-clean", body).onclick = async () => {
      const ok = await confirmDialog({title: "Clean unreferenced files", button: "Clean", danger: true, id: "storage-clean-dialog",
        body: `<p>Remove ${f.unreferenced} stored file${f.unreferenced === 1 ? "" : "s"} nothing references, freeing ${fmtBytes(f.unreferenced_bytes)}?</p>
          <p class="hint">Files any sheet, view, build or version still uses are never removed.</p>`});
      if (!ok) return;
      try { await api(`/api/projects/${slug}/storage/clean`, {}); } catch (err) { alert(errorText(err)); }
      draw();
    };
  };
  draw();
}

// --- Tools › Settings… -------------------------------------------------------------------
async function settingsDialog() {
  if (noProject()) return;
  let s;
  try { s = await api(`/api/projects/${slug}/settings`, undefined, "GET"); }
  catch (err) { alert(errorText(err)); return; }
  const body = document.createElement("div"); body.id = "settings-body";
  body.innerHTML = `<div class="form"><label for="set-keep">Versions to keep:</label>
      <input id="set-keep" type="number" min="1" step="1" value="${s.versions_keep}" style="width:5em"></div>
    <p class="hint">Every build of an asset is kept as a version. After a build, versions beyond the last N are offered for
      removal in the asset's Versions box (nothing is deleted without asking). The Active and Approved versions are never removed.
      Set per project (default ${s.default}).</p><p class="error" id="set-err"></p>`;
  const dlg = dialog({title: "Settings", body, className: "tool-window", buttons: [
    {label: "Save", default: true, onClick: async () => {
      const v = Number($("#set-keep", body).value);
      if (!Number.isInteger(v) || v < 1) { $("#set-err", body).textContent = "Versions to keep must be a whole number, 1 or more."; return false; }
      try { await api(`/api/projects/${slug}/settings`, {versions_keep: v}, "PUT"); }
      catch (err) { $("#set-err", body).textContent = errorText(err); return false; }
    }}, {label: "Cancel"}]});
  dlg.win.id = "settings-dialog";
}

// Pipeline › Smooth TRELLIS models… / Remove ground from TRELLIS models…: every asset whose Active
// version is a TRELLIS model that still needs it; a tick adds the other fix in the same pass
async function fixAllDialog(mode) {
  let c;
  try { c = await api(`/api/projects/${slug}/library/smooth`, undefined, "GET"); } catch (err) { alert(errorText(err)); return; }
  const ground = mode === "ground", mine = ground ? c.ground : c.assets, other = ground ? c.assets : c.ground;
  if (!mine.length) { alert(ground ? "Every TRELLIS model in use has had its ground removed or checked." : "Every TRELLIS model in use is smooth already."); return; }
  const body = document.createElement("div");
  body.innerHTML = (ground
    ? `<p>Cuts off a thin ground slab (a shadow or floor TRELLIS built under the model) from the Active TRELLIS model of: ${mine.map(esc).join(", ")}. Plinths, podiums and steps stay.</p>`
    : `<p>Welds and shades smooth by angle (${c.angle}°) the Active TRELLIS model of: ${mine.map(esc).join(", ")}.</p>`)
    + `<p>Each becomes a new version, Active in place of the old one (still there to go back to). A few seconds each.${ground ? " Where no ground is found, nothing changes and the version says so." : ""}</p>`
    + (other.length ? `<label class="check"><input type="checkbox" id="fix-also"> ${ground ? "Smooth the shading first, in the same pass" : "Also remove the ground, in the same pass"} (${other.length} model${other.length === 1 ? "" : "s"})</label>` : "");
  if (!await confirmDialog({title: ground ? "Remove ground from TRELLIS models" : "Smooth TRELLIS models",
    button: `${ground ? "Remove ground from" : "Smooth"} ${mine.length}`, id: ground ? "ground-all-dialog" : "smooth-all-dialog", body})) return;
  const also = !!(body.querySelector("#fix-also") || {}).checked;
  try { await api(`/api/projects/${slug}/library/smooth`, {smooth: !ground || also, ground: ground || also}); } catch (err) { alert(errorText(err)); }
  await load();
}

Object.assign(ACTIONS, {storage: {run: storageDialog, enabled: () => !!slug}, settings: {run: settingsDialog, enabled: () => !!slug},
  smoothAll: {run: () => fixAllDialog("smooth"), enabled: () => !!slug}, groundAll: {run: () => fixAllDialog("ground"), enabled: () => !!slug}});
{
  const tools = MENUS.find(m => m[0] === "Tools")[2];
  tools.splice(tools.length, 0, "-", ["Storage…", "storage"], ["Settings…", "settings"]);
  const pipe = MENUS.find(m => m[0] === "Pipeline")[2];
  pipe.splice(pipe.findIndex(x => x[1] === "make3d") + 1, 0, ["Smooth TRELLIS models…", "smoothAll"], ["Remove ground from TRELLIS models…", "groundAll"]);
}
