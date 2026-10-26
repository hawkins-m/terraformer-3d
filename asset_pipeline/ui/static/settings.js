// Settings (File › Settings…, the gear in the toolbar, and the Status box's "missing key" link).
// Keys & connections: every setting on the setup wizard's Connections page (setup.ITEMS with
// wizard = "connections"), editable, each with a Test; API keys masked, with Show. Save writes the
// user config and secrets.toml (ui/setup_api.py); key values are never sent back to the page.

const SETTINGS_PAGES = [["keys", "Keys & connections"]];

async function appSettingsDialog(page = "keys") {
  const body = document.createElement("div"); body.className = "settings";
  body.innerHTML = `<p class="hint">Checking this machine…</p>`;
  let save = async () => false;
  const d = dialog({title: "Settings", body, className: "tool-window settings-window", buttons: [
    {label: "Save", default: true, onClick: () => save()}, {label: "Cancel"}]});
  d.win.id = "app-settings-dialog";
  let st;
  try { st = await api("/api/setup", undefined, "GET"); }
  catch (err) { body.innerHTML = `<span class="error">${esc(errorText(err))}</span>`; return; }
  const items = st.items.filter(it => it.wizard === "connections");
  const field = it => {
    const id = `st-${it.id}`, v = it.value || it.detected || "";
    if (it.kind === "choice") return `<select id="${id}" data-id="${it.id}">${it.choices.map(c => `<option value="${esc(c)}"${c === v ? " selected" : ""}>${esc((it.labels || {})[c] || c)}</option>`).join("")}</select>`;
    if (it.kind === "secret") return `<input id="${id}" data-id="${it.id}" data-secret="1" type="password" autocomplete="off" spellcheck="false"
        placeholder="${it.has_value ? "set (type to replace it)" : "not set"}"><button type="button" class="small st-show" aria-pressed="false">Show</button>`;
    return `<input id="${id}" data-id="${it.id}" type="text" spellcheck="false" value="${esc(v)}">`;
  };
  body.innerHTML = `<nav class="settings-nav" aria-label="Settings pages">${SETTINGS_PAGES.map(([k, label]) =>
      `<button type="button" class="${k === page ? "on" : ""}" data-page="${k}">${esc(label)}</button>`).join("")}</nav>
    <section class="settings-page" aria-label="Keys & connections">
      <p class="hint" style="margin:0 0 8px">127.0.0.1 is this computer: Terraformer talks to ComfyUI here, and nothing is sent online.
        Keys go to <code>${esc(st.secrets)}</code> (only you can read it); the rest to <code>${esc(st.config)}</code>.</p>
      <div class="setup-grid">${items.map(it => `<div class="setup-item" data-id="${it.id}"${it.shown ? "" : " hidden"}>
        <label for="st-${it.id}"><b>${esc(it.label)}</b>${it.optional ? ` <span class="hint" style="margin:0">optional</span>` : ""}${
          it.kind === "secret" ? ` <span class="hint" style="margin:0">${it.has_value ? "· set" : "· not set"}</span>` : ""}</label>
        <div class="row setup-row">${field(it)}<button type="button" class="small st-test">Test</button></div>
        <div class="hint su-help" style="margin:0">${esc(it.help)}</div>
        <div class="su-result" role="status" aria-live="polite"></div></div>`).join("")}</div>
    </section>`;
  const values = () => Object.fromEntries([...body.querySelectorAll("[data-id]")].filter(e => e.matches("input, select") && !e.dataset.secret)
    .map(e => [e.dataset.id, e.value.trim()]));
  const shown = () => {                               // the proxy and its key follow the backend
    const backend = ($("#st-comfy_backend", body) || {}).value || "sdk";
    for (const it of items) {
      const row = $(`.setup-item[data-id="${it.id}"]`, body);
      if (it.when === "backend=sdk") row.hidden = backend !== "sdk";
      else if (it.when === "backend=httpx") row.hidden = backend !== "httpx";
    }
  };
  ($("#st-comfy_backend", body) || {}).onchange = shown;
  shown();
  const test = async id => {
    const row = $(`.setup-item[data-id="${id}"]`, body), out = $(".su-result", row), el = $(`#st-${id}`, body);
    out.className = "su-result hint"; out.textContent = "Testing…";
    try {
      const r = await api("/api/setup/test", {id, value: el.dataset.secret ? el.value : el.value.trim(), values: values()});
      out.className = `su-result ${r.ok ? (r.warn ? "su-warn" : "ok") : "error"}`;
      out.textContent = `${r.ok ? (r.warn ? "Note: " : "✓ ") : "✗ "}${r.message}`;
    } catch (err) { out.className = "su-result error"; out.textContent = errorText(err); }
  };
  body.querySelectorAll(".st-test").forEach(b => b.onclick = () => test(b.closest(".setup-item").dataset.id));
  body.querySelectorAll(".st-show").forEach(b => b.onclick = () => {
    const el = b.previousElementSibling, show = el.type === "password";
    el.type = show ? "text" : "password";
    b.textContent = show ? "Hide" : "Show";
    b.setAttribute("aria-pressed", String(show));
  });
  save = async () => {
    const vals = values();
    for (const it of items) if ($(`.setup-item[data-id="${it.id}"]`, body).hidden) delete vals[it.id];
    const secrets = {};
    body.querySelectorAll("[data-secret]").forEach(e => {
      if (e.value.trim()) secrets[items.find(it => it.id === e.dataset.id).key.replace(/^secret:/, "")] = e.value.trim();
    });
    try {
      await api("/api/setup/save", {values: vals, secrets});
      if (typeof fetchDoctor === "function") fetchDoctor(true);      // the Status box's "missing key" goes away
    } catch (err) { alert(errorText(err)); return false; }
  };
}

ACTIONS.appSettings = {run: () => appSettingsDialog()};
{
  const file = MENUS.find(m => m[0] === "File")[2];
  file.splice(file.findIndex(x => x[1] === "refresh"), 0, ["Settings…", "appSettings"], "-");
}
$("#tb-settings").onclick = () => appSettingsDialog();
