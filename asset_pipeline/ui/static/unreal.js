// Unreal tab: "Build city in Unreal" on top (`ap ue build` as one CPU job, its last report below
// it); under "Advanced steps" the numbered single steps (create the UE project, export, import,
// render shots, match look), each enabled only when it can run; then the UE project backup.
// Import, renders and look scenes open the Unreal menu's dialogs (world.js); all run as CPU-lane jobs.
const UE_JOBS = ["ue.build", "ue.export", "ue.backup"];
let unrealData = null;
const ueExportStale = () => !!(unrealData && unrealData.export && statusData &&
  unrealData.export.mtime + 1 < (statusData.greybox_mtime || 0));

// ueSteps (the numbered steps' state) lives in workflow.js: the Unreal stage's status reads it too.

// Why the build can't start ("" = it can): the editor holding the project comes first.
const ueBuildWhy = u => u.editor_open ? "The Unreal editor has the project open: close it to build."
  : !u.greybox ? "Build the greybox first (Greybox)." : "";

// The last build's report: placed, stand-ins left, time, placement check, warnings.
function ueBuildReport(r) {
  if (!r) return "";
  const n = (k, one, many) => `${k} ${k === 1 ? one : many}`;
  const head = `<b>${r.ok ? "Built" : "Built with problems"}</b> ${esc(new Date(r.finished * 1000).toLocaleString())}` +
    (r.use_active ? " · Active versions (preview)" : "") + (r.keep_hand_moves === false ? " · layout reset" : "");
  const facts = [n(r.placed, "asset placed", "assets placed"), n(r.greybox_slots, "stand-in left", "stand-ins left"),
    `${fmtDur(r.seconds)}`].concat(r.held ? [`${r.held} held back (not approved or not fully built)`] : [])
    .concat(r.shots != null ? [n(r.shots, "shot rendered", "shots rendered")] : []);
  const place = r.placement_lines || ["placement not checked (no library actors read back)"];
  const placeOk = r.placement && r.placement.ok;
  return `<div>${head}</div><div>${esc(facts.join(" · "))}</div>` +
    `<div class="${placeOk ? "" : "error"}">${esc(place[0])}</div>` +
    (place.length > 1 ? `<ul>${place.slice(1).map(l => `<li>${esc(l.trim())}</li>`).join("")}</ul>` : "") +
    ((r.active_models || []).length ? `<ul>${r.active_models.map(l => `<li>${esc(l)}</li>`).join("")}</ul>` : "") +
    dressReport(r.dressing) +
    (r.warnings.length ? `<div>${n(r.warnings.length, "warning", "warnings")}:</div><ul>${r.warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul>` : "");
}

function renderUnreal() {
  const u = unrealData;
  if (!u) return;
  renderReadiness();
  const building = activeJobs("ue.build"), buildWhy = ueBuildWhy(u);
  $("#ue-build").disabled = !!buildWhy || !!building.length;
  $("#ue-build").textContent = building.length ? "Building…" : "Build city in Unreal";
  $("#ue-build-state").innerHTML = buildWhy ? `<span class="${u.editor_open ? "error" : "ue-why"}">${esc(buildWhy)}</span>`
    : building.length ? "Progress and Stop: Jobs panel." : "";
  setStop("#ue-build-stop", building);
  if (dressFor !== slug) loadDressing();
  $("#ue-build-report").hidden = !u.build;
  $("#ue-build-report").innerHTML = ueBuildReport(u.build);
  const steps = ueSteps(u), running = k => activeJobs(k).length > 0;
  for (const [k, [state, done, why]] of Object.entries(steps)) {
    const li = $(`#ue-steps [data-step="${k}"]`), btn = $(".ue-do button", li);
    li.classList.toggle("done", done);
    if (k !== "export") {
      btn.disabled = !!why || running(`ue.${k}`);
      $(".ue-state", li).innerHTML = (state ? esc(state) : "") + (why && k !== "init" ? `<span class="ue-why">${esc(why)}</span>` : "");
    }
  }
  $("#ue-look").textContent = (u.looks || []).length ? "Match look…" : "Create look preset…";
  const exporting = activeJobs("ue.export"), backingUp = activeJobs("ue.backup");
  const e = u.export;
  $("#ue-export-state").innerHTML = !u.greybox ? `<span class="ue-why">${esc(steps.export[2])}</span>` :
    !e ? "Not exported yet." :
    `${e.assets} assets, ${e.slots} slots, ${e.instances.toLocaleString("en")} instances, ${e.shots} shots · ` +
    `${new Date(e.mtime * 1000).toLocaleString()}` +
    (ueExportStale() ? ` · <span class="error">greybox changed since: export again</span>` : "");
  $("#ue-export").disabled = !u.greybox || !!exporting.length;
  $("#ue-export").textContent = exporting.length ? "Exporting…" : e ? "Export again" : "Export to Unreal";
  setStop("#ue-export-stop", exporting);

  $("#ue-project").textContent = u.uproject;
  $("#ue-backup-state").innerHTML = !u.project ? `No UE project yet (Unreal › Create UE project).` :
    (u.backups.length ? `${u.backups.length} snapshot${u.backups.length === 1 ? "" : "s"}, newest ${esc(u.backups.at(-1))}`
      : "No snapshots yet.") + ` · ${esc(u.backup_dir)}` +
    (u.editor_open ? `<br><span class="error">An Unreal Editor has the project open: save and close it before backing up.</span>` : "");
  $("#ue-backup").disabled = !u.project || !!backingUp.length;
  $("#ue-backup").textContent = backingUp.length ? "Backing up…" : "Back up now";
  setStop("#ue-backup-stop", backingUp);
}

async function ueExport() {
  try { await api(`/api/projects/${slug}/export`, {}); } catch (err) { alert(errorText(err)); }
  await load();
}

async function ueBackup(force = false) {
  try { await api(`/api/projects/${slug}/ue/backup`, {force}); }
  catch (err) {
    if (!force && err.message.includes("open:") &&
        confirm(`${errorText(err)}.\n\nBack up anyway? A snapshot taken while the editor saves can be inconsistent.`))
      return ueBackup(true);
    if (force || !err.message.includes("open:")) alert(errorText(err));
  }
  await load();
}

// `ap ue build` as a job (api() asks first when the estimate is long); the report shows on the tab.
async function ueBuild() {
  const body = {use_active: $("#ueb-active").checked, keep_hand_moves: $("#ueb-keep").checked, render: $("#ueb-render").checked,
    dressing: dressData ? dressOpts() : null};
  if (!body.keep_hand_moves && !(await confirmDialog({title: "Reset layout", button: "Build and reset", danger: true, id: "ue-build-reset",
    body: "<p>Every actor goes back to the plan's spot: moves you made in Unreal are lost (the build backs up the UE project first).</p>"}))) return;
  let j;
  try { j = await api(`/api/projects/${slug}/ue/build`, body); await load(); j = await jobDone(j); }
  catch (err) { alert(errorText(err)); await load(); return; }
  await load();
  if (j.status === "error") alert(errorText(j.error));
}

$("#ue-build").onclick = () => ueBuild();
// Use Active versions: what the next build places changes with it (Ready for Unreal, Generate 3D Model & Review's line per asset)
$("#ueb-active").onchange = async () => { await loadNextBuild(); renderReadiness(); if (typeof renderReview === "function") renderReview(); };

// Ready for Unreal: counts and one row per asset the city uses (next_build.explain via nextBuildData);
// a name opens the asset in Generate 3D Model & Review (jump.js .jump-link). Redrawn only when its data changes.
function renderReadiness() {
  const box = $("#ue-ready-table"), cn = $("#ue-ready-counts"), nb = nextBuildData;
  const sig = JSON.stringify(nb);
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  if (!nb) { cn.textContent = "Couldn't read what the next build places."; box.innerHTML = ""; return; }
  if (!nb.greybox) { cn.textContent = "No greybox yet: build it first (Greybox). Every asset waits as a greybox stand-in."; box.innerHTML = ""; return; }
  const rows = nb.assets.filter(r => r.role !== "none"), off = nb.assets.length - rows.length, c = nb.counts;
  const n = (k, one, many = one + "s") => `${k} ${k === 1 ? one : many}`;
  cn.innerHTML = [`<b>${c.approved}</b> approved`, `<b>${c.active_not_approved}</b> active but not approved${nb.use_active ? " (placed: Use Active versions)" : ""}`,
    `<b>${c.stand_ins}</b> stand-in${c.stand_ins === 1 ? "" : "s"}`,
    nb.placement_checked ? `<b>${c.placement_issues}</b> with placement-check issues` : "placement not checked yet (no build)"].join(" · ")
    + (off ? ` <span class="hint" style="margin:0">(${n(off, "other asset")} not in the city layout)</span>` : "");
  if (!rows.length) { box.innerHTML = `<p class="hint">No library asset stands in the city layout yet.</p>`; return; }
  const cleanup = r => r.version == null && r.model == null ? "–"
    : r.model === "procedural" ? "procedural build" : r.model === "cleaned" ? `cleaned for ${r.usage ? usageLabel(r.usage) : "its tier"} (your choice)`
    : r.cleanup_available ? "raw (a cleaned version exists, not chosen)" : "not cleaned (optional)";
  const issues = r => Object.entries(r.placement || {}).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(", ");
  box.innerHTML = `<table class="grid-table"><thead><tr><th>Asset</th><th>Version used</th><th>Approved</th><th>Cleanup</th><th>Stand-in reason</th><th>Placement issues</th></tr></thead><tbody>
    ${rows.map(r => `<tr data-asset="${esc(r.entry)}" class="${r.placed ? "" : "ue-standin"}">
      <td><a href="#" class="jump-link" data-jump="views" data-asset="${esc(r.entry)}" title="Open it in Generate 3D Model & Review">${esc(assetName(r.entry, r.name))}</a></td>
      <td title="${esc(r.why || "")}">${r.placed ? (r.version != null ? `v${r.version}` : "build on disk") : "–"}</td>
      <td>${r.approved ? "yes" : "no"}</td><td>${esc(cleanup(r))}</td>
      <td>${r.placed ? "" : esc(r.stand_in || "")}</td>
      <td class="${issues(r) ? "error" : ""}">${nb.placement_checked ? esc(issues(r) || "none") : "–"}</td></tr>`).join("")}</tbody></table>`;
}
$("#ue-export").onclick = () => ueExport();
$("#ue-init").onclick = () => ACTIONS.ueInit.run();
$("#ue-import").onclick = () => ueImportDialog();
$("#ue-render").onclick = () => ueRenderDialog();
$("#ue-look").onclick = () => ((unrealData && unrealData.looks || []).length ? ueLookDialog() : ueLookNewDialog());

// No look preset yet: make one from an approved shot (locked on a frame), then open Look scene.
function ueLookNewDialog() {
  const shots = (unrealData && unrealData.approved_shots) || [];
  if (!shots.length) { alert("Lock a shot on its approved frame first (Frames › Use this frame for this shot)."); return; }
  const {form} = formDialog({title: "Create a look preset", id: "ue-look-new-dialog",
    html: `<label for="ln-shot">Approved shot:</label><select id="ln-shot">${optionList(shots)}</select>
      <p class="hint" style="grid-column:1/-1">Writes site/look_tests/&lt;shot&gt;.json for that shot; the look scene matches
        light, ground and exposure to its locked frame. Edit the preset later for sun, fog or exposure.</p>`,
    buttons: [{label: "Create", default: true, onClick: async () => {
      try { await api(`/api/projects/${slug}/ue/looks/new`, {shot: $("#ln-shot", form).value}); }
      catch (err) { alert(errorText(err)); return false; }
      await load(); setTimeout(ueLookDialog, 0);
    }}, {label: "Cancel"}]});
}
$("#ue-backup").onclick = () => ueBackup();

// --- World dressing (under Build): options remembered per project, sent with each build ------------
// GET ue/dressing gives the options, the Megascans mapping, each option's label, performance tag and
// tooltip, the CC0 defaults' state and whether this Unreal has the Fab plugin.
let dressData = null, dressFor = null, dressSaveT = null;
const DRESS_CHECKS = {ground: "#dr-ground", rocks: "#dr-rocks", vegetation: "#dr-vegetation", megascans: "#dr-megascans"};

async function loadDressing() {
  dressFor = slug;
  if (!slug) return;
  try { dressData = await api(`/api/projects/${slug}/ue/dressing`, undefined, "GET"); }
  catch (err) { dressData = null; $("#dr-state").textContent = `World dressing unavailable: ${errorText(err)}`; return; }
  const o = dressData.options;
  for (const [k, sel] of Object.entries(DRESS_CHECKS)) $(sel).checked = !!o[k];
  $("#dr-rock-density").value = o.rock_density;
  $("#dr-veg-density").value = o.vegetation_density;
  for (const r of document.querySelectorAll('input[name="dr-water"]')) r.checked = r.value === o.water;
  renderDressing();
}

function dressOpts() {
  const w = document.querySelector('input[name="dr-water"]:checked');
  return {ground: $("#dr-ground").checked, rocks: $("#dr-rocks").checked, rock_density: +$("#dr-rock-density").value,
    vegetation: $("#dr-vegetation").checked, vegetation_density: +$("#dr-veg-density").value,
    water: w ? w.value : "off", megascans: $("#dr-megascans").checked, seed: dressData ? dressData.options.seed : 1};
}

// The current Megascans mapping, one role per line (the Megascans tooltip).
function dressMapping() {
  const lib = dressData.library || {};
  const lines = Object.keys(dressData.roles).map(r => `${r}: ${lib[r] ? lib[r].split("/").pop().split(".")[0] : "(free CC0 / fallback)"}`);
  return `Mapping:\n${lines.join("\n")}`;
}

function renderDressing() {
  if (!dressData) return;
  const L = dressData.labels, o = dressOpts();
  for (const el of document.querySelectorAll("#ue-dress .perf")) el.textContent = (L[el.dataset.perf] || {}).perf || "";
  const tip = (sel, k) => { const lab = $(sel).closest("label"); lab.title = L[k].tip; };
  tip("#dr-ground", "ground"); tip("#dr-rocks", "rocks"); tip("#dr-vegetation", "vegetation");
  for (const r of document.querySelectorAll('input[name="dr-water"]'))
    r.closest("label").title = r.value === "off" ? "Leave the sea plane as it is." : L[`water_${r.value}`].tip;
  $("#dr-megascans").closest("label").title = `${L.megascans.tip}\n\n${dressData.plugin.help}\n\n${dressMapping()}`;
  $("#dr-find").title = "Scan the Unreal project's Content (/Game/Fab, /Game/Megascans, /Game/Quixel and your folders) " +
    "for Megascans meshes, foliage and surfaces, and propose which role each fills.\n\n" + dressData.plugin.help;
  $("#dr-rock-density").disabled = !o.rocks;
  $("#dr-veg-density").disabled = !o.vegetation;
  $("#dr-rock-density").nextElementSibling.textContent = `×${o.rock_density.toFixed(2)}`;
  $("#dr-veg-density").nextElementSibling.textContent = `×${o.vegetation_density.toFixed(2)}`;
  // what the free defaults still need to download (on the first build that uses them)
  const used = Object.entries(dressData.roles).filter(([, v]) => o[v.option]).map(([r]) => r);
  const missing = used.filter(r => dressData.cc0[r] && dressData.cc0[r].state !== "installed");
  const mapped = o.megascans ? used.filter(r => (dressData.library || {})[r]).length : 0;
  $("#dr-state").textContent = !used.length && o.water === "off" ? "No dressing: the build removes what an earlier one placed." :
    [mapped ? `${mapped} of ${used.length} roles from Megascans` : "",
     missing.length ? `${missing.length} free CC0 asset${missing.length === 1 ? "" : "s"} download on the first build` : "",
     o.water === "ocean" ? "Ocean enables the Water plugin in the .uproject" : ""].filter(Boolean).join(" · ");
}

function dressChanged() {
  renderDressing();
  clearTimeout(dressSaveT);
  dressSaveT = setTimeout(async () => {
    try { await api(`/api/projects/${slug}/ue/dressing`, dressOpts()); dressData.options = dressOpts(); }
    catch (err) { $("#dr-state").textContent = `Not saved: ${errorText(err)}`; }
  }, 400);
}
for (const el of document.querySelectorAll("#ue-dress input")) el.addEventListener(el.type === "range" ? "input" : "change", dressChanged);

// Build report: which options ran, instances per role, the source of each role.
function dressReport(d) {
  if (!d || !d.ran) return "";
  if (!d.ran.length) return `<div>World dressing: off${(d.removed || []).length ? ` (removed ${esc(d.removed.join(", "))})` : ""}</div>`;
  const counts = Object.entries(d.counts || {}).filter(([, n]) => n).map(([r, n]) => `${r} ${n.toLocaleString("en")}`);
  const by = {};
  for (const [r, s] of Object.entries(d.sources || {})) (by[s] = by[s] || []).push(r);
  return `<div>World dressing: ${esc(d.ran.join(", "))}${d.placed === false ? " (instances unchanged)" : ""}</div><ul>` +
    (counts.length ? `<li>${esc(counts.join(", "))}</li>` : "") +
    Object.entries(by).map(([s, rs]) => `<li>${esc(s)}: ${esc(rs.join(", "))}</li>`).join("") +
    (d.water ? `<li>water: ${esc(d.water)}</li>` : "") +
    (d.warnings || []).map(w => `<li class="error">${esc(w)}</li>`).join("") + "</ul>";
}

// Find my assets: scan, then a role -> asset table to edit (a list of what was found, or a path typed in).
async function dressFind() {
  if (!dressData) await loadDressing();
  let s;
  try { s = await api(`/api/projects/${slug}/ue/dressing/scan`, {}); }
  catch (err) { alert(errorText(err)); return; }
  const lib = s.library || {};
  const rows = Object.entries(dressData.roles).map(([r, v]) => {
    const cands = s.candidates[r] || [], cur = lib[r] || "", pick = cur || s.proposal[r] || "";
    const opts = [["", "(none: free CC0 / fallback)"], ...cands.map(c => [c.path, `${c.name} (${c.class.replace("_", " ")})`])];
    if (cur && !cands.some(c => c.path === cur)) opts.push([cur, `${cur.split("/").pop()} (current, not found)`]);
    return `<tr><th scope="row" title="${esc(v.what)}">${esc(r)}</th><td><select data-role="${esc(r)}" data-initial="${esc(pick)}" aria-label="${esc(r)}">` +
      opts.map(([p, t]) => `<option value="${esc(p)}"${p === pick ? " selected" : ""}>${esc(t)}</option>`).join("") +
      `</select></td><td>${cands.length} found${!cur && s.proposal[r] ? " · proposed" : ""}</td></tr>`;
  }).join("");
  const found = Object.values(s.candidates).reduce((n, c) => n + c.length, 0);
  formDialog({title: "Megascans for world dressing", id: "dress-map-dialog",
    html: `<p class="hint" style="grid-column:1/-1">Scanned ${s.folders_scanned.length} folder(s) in ${esc(s.content)}: ${found} matching asset(s).
        ${found ? "" : esc(dressData.plugin.help)}</p>
      <table class="dress-map" style="grid-column:1/-1"><thead><tr><th>Role</th><th>Unreal asset</th><th></th></tr></thead><tbody>${rows}</tbody></table>
      <label for="dm-folders">Also scan:</label><input id="dm-folders" placeholder="/Game/MyPacks, /Game/Environment" value="${esc((dressData.folders || []).join(", "))}">
      <label for="dm-path">Or type a path:</label><input id="dm-path" placeholder="role=/Game/Fab/.../SM_Name.SM_Name">`,
    buttons: [{label: "Save mapping", default: true, onClick: async win => {
      const library = {};
      // "" only where you changed a role to none: roles nothing was found for stay open to later scans
      for (const sel of win.querySelectorAll("select[data-role]"))
        if (sel.value || sel.value !== sel.dataset.initial || (sel.dataset.role in lib)) library[sel.dataset.role] = sel.value;
      const typed = $("#dm-path", win).value.trim().match(/^(\w+)\s*=\s*(\/Game\/\S+)$/);
      if (typed) library[typed[1]] = typed[2];
      const folders = $("#dm-folders", win).value.split(",").map(x => x.trim()).filter(Boolean);
      try { const r = await api(`/api/projects/${slug}/ue/dressing/library`, {library, folders}); dressData.library = r.library; dressData.folders = folders; }
      catch (err) { alert(errorText(err)); return false; }
      renderDressing();
    }}, {label: "Scan again", onClick: async win => {
      const folders = $("#dm-folders", win).value.split(",").map(x => x.trim()).filter(Boolean);
      try { await api(`/api/projects/${slug}/ue/dressing/scan`, {folders}); dressData.folders = folders; } catch (err) { alert(errorText(err)); }
      setTimeout(dressFind, 0);
    }}, {label: "Cancel"}]});
}
$("#dr-find").onclick = () => dressFind();
