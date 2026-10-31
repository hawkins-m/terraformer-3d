// File > Manage projects…: every project with its facts and disk size (project folder, Unreal project,
// Unreal backups), and Open, Rename, Duplicate, Delete and a Protected toggle per row. Delete moves to
// the system trash and shows what goes and how much space it frees; the open project and protected
// ones can't be deleted. Server side: asset_pipeline/project_manager.py.

const manageSize = b => b ? fmtGB(b) : "–";
const manageDate = t => t ? new Date(t * 1000).toLocaleDateString() : "never";

async function manageProjects() {
  let rows = [], dlg = null;
  const body = document.createElement("div");
  body.innerHTML = `<p class="hint">Delete moves a project to the system trash (Recycle Bin on Windows); you can still restore it from there.</p>
    <div class="manage-scroll"><table class="grid-table manage-table"><thead><tr><th>Name</th><th>Layout</th><th>City size</th>
    <th class="num" title="The project folder">Project</th><th class="num" title="Its Unreal project, outside the project folder">Unreal</th>
    <th class="num" title="Its Unreal backups">Backups</th><th>Last opened</th><th>Approved assets</th><th>Protected</th><th>Actions</th></tr></thead>
    <tbody></tbody></table></div><p class="hint manage-msg" role="status"></p>`;
  const say = t => { $(".manage-msg", body).textContent = t || ""; };
  const reload = async () => {
    say("Measuring…");
    try { rows = await api("/api/manage/projects", undefined, "GET"); say(""); } catch (e) { say(errorText(e)); return; }
    paint();
  };
  const paint = () => {
    const tb = $("tbody", body); tb.replaceChildren();
    if (!rows.length) { tb.innerHTML = `<tr><td colspan="10">No projects yet.</td></tr>`; return; }
    for (const r of rows) {
      const tr = document.createElement("tr"); tr.dataset.slug = r.slug;
      if (r.error) { tr.innerHTML = `<td>${esc(r.slug)}</td><td colspan="9">Unreadable: ${esc(r.error)}</td>`; tb.append(tr); continue; }
      const b = r.bytes || {}, isOpen = r.slug === slug;
      tr.innerHTML = `<td title="Folder: ${esc(r.slug)}">${esc(r.name)}${isOpen ? " <em>(open)</em>" : ""}</td>
        <td>${esc(r.preset || "–")}</td><td>${esc(r.size || "–")}</td>
        <td class="num">${manageSize(b.project)}</td><td class="num">${manageSize(b.unreal)}</td><td class="num">${manageSize(b.backups)}</td>
        <td>${manageDate(r.opened)}</td><td>${r.approved ? "Yes" : "No"}</td>
        <td><label title="A protected project can't be deleted${r.protected_set === null && r.approved ? " (on by default: it has approved assets)" : ""}."><input type="checkbox" class="m-prot" ${r.protected ? "checked" : ""}> Protected</label></td>
        <td class="manage-actions"><button type="button" class="m-open">Open</button> <button type="button" class="m-ren">Rename…</button>
          <button type="button" class="m-dup">Duplicate</button> <button type="button" class="m-del">Delete…</button></td>`;
      const del = $(".m-del", tr);
      if (r.protected) del.title = "Protected: turn Protected off first.";
      else if (isOpen) del.title = "This project is open: switch to another first.";
      $(".m-open", tr).disabled = isOpen;
      $(".m-open", tr).onclick = () => {
        if (!confirmDiscard()) return;
        dlg.close(); slug = r.slug; localStorage.setItem("ap.project", slug); $("#project").value = slug; load();
      };
      $(".m-prot", tr).onchange = async e => {
        try { await api(`/api/manage/projects/${r.slug}/protect`, {protected: e.target.checked}); await reload(); }
        catch (err) { say(errorText(err)); e.target.checked = !e.target.checked; }
      };
      $(".m-ren", tr).onclick = () => manageRename(r, async () => { await reload(); await loadProjects(slug); });
      $(".m-dup", tr).onclick = async e => {
        e.target.disabled = true; say(`Duplicating ${r.name}…`);
        try { const j = await api(`/api/manage/projects/${r.slug}/duplicate`, {}); await reload(); await loadProjects(slug); say(`Made ${j.slug}.`); }
        catch (err) { say(errorText(err)); } finally { e.target.disabled = false; }
      };
      del.onclick = () => manageDelete(r, async msg => { await reload(); await loadProjects(slug); say(msg); });
      tb.append(tr);
    }
  };
  dlg = dialog({title: "Manage projects", body, className: "manage", buttons: [{label: "Close", default: true}]});
  reload();
}

function manageRename(r, done) {
  const f = document.createElement("div");
  f.innerHTML = `<p>Display name only; the folder <b>${esc(r.slug)}</b> stays.</p><label>Name: <input type="text" class="m-name" size="32"></label>`;
  const input = $(".m-name", f); input.value = r.name;
  dialog({title: "Rename project", body: f, buttons: [{label: "Rename", default: true, onClick: async () => {
    try { await api(`/api/manage/projects/${r.slug}/rename`, {name: input.value}); await done(); }
    catch (e) { alert(errorText(e)); return false; }
  }}, {label: "Cancel"}]});
}

async function manageDelete(r, done) {
  let plan;
  try { plan = await api(`/api/manage/projects/${r.slug}/delete-plan?current=${encodeURIComponent(slug || "")}`, undefined, "GET"); }
  catch (e) { alert(errorText(e)); return; }
  if (plan.blocked) { dialog({title: "Can't delete", body: `<p>${esc(plan.blocked)}</p>`}); return; }
  const f = document.createElement("div");
  f.innerHTML = `<p>Move <b>${esc(plan.name)}</b> (${esc(plan.slug)}) to the trash?</p><ul class="manage-items"></ul>
    <p class="manage-free"></p><p class="hint">Nothing is deleted for good. The space is freed when the trash is emptied.</p>`;
  const boxes = {};
  for (const i of plan.items) {
    const li = document.createElement("li");
    const usable = i.exists && !i.reason;
    li.innerHTML = `<label><input type="checkbox" ${i.always ? "checked disabled" : ""} ${usable || i.always ? "" : "disabled"}>
      ${esc(i.label)} (${i.exists ? fmtGB(i.bytes) : "not there"})</label> <span class="hint">${esc(i.path)}${i.reason ? ` – not offered: ${esc(i.reason)}` : ""}</span>`;
    boxes[i.key] = $("input", li); $(".manage-items", f).append(li);
  }
  const free = () => { const n = plan.items.filter(i => boxes[i.key].checked).reduce((a, i) => a + i.bytes, 0);
    $(".manage-free", f).textContent = `Frees about ${fmtGB(n)} once the trash is emptied.`; };
  Object.values(boxes).forEach(b => b.onchange = free); free();
  dialog({title: "Delete project", body: f, buttons: [{label: "Move to trash", default: true, onClick: async () => {
    try {
      const j = await api(`/api/manage/projects/${r.slug}/delete`, {unreal: boxes.unreal.checked, backups: boxes.backups.checked, current: slug});
      await done(`Moved to the trash: ${j.removed.join(", ")}.`);
    } catch (e) { alert(errorText(e)); return false; }
  }}, {label: "Cancel"}]});
}
