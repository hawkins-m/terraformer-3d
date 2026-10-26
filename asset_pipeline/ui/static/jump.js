// Library and Views and 3D lists: the header (tabs, instructions, toolbar) stays fixed and only the
// list scrolls (style.css). Each list keeps its own scroll position per project, across sub-tab
// switches, re-renders and reloads. "Jump to" boxes (Areas too) centre and mark an asset's row; an
// asset's name in Asset review opens its row in Reference sheets, and the other way round.
// Loaded after library.js, refs.js, review.js, shell.js (render functions, showSub, showTab).
const JUMP_LISTS = {assets: "#assets-list", refs: "#refs-list", views: "#review-list"};
const jumpState = {pos: {}, slug: null, saveT: null, hit: null};   // hit: the jumped-to row {el, k, id}

function jumpLoadPositions() {
  if (jumpState.slug === slug) return;
  jumpState.slug = slug; jumpState.pos = {};
  try { jumpState.pos = JSON.parse(localStorage.getItem(`ap.scroll.${slug}`) || "{}") || {}; } catch (_) { /* ignore */ }
}
function jumpSavePositions() {
  clearTimeout(jumpState.saveT);
  jumpState.saveT = setTimeout(() => { try { localStorage.setItem(`ap.scroll.${slug}`, JSON.stringify(jumpState.pos)); } catch (_) { /* ignore */ } }, 300);
}
function jumpRestore(k) {
  jumpLoadPositions();
  const el = $(JUMP_LISTS[k]);
  if (el && el.offsetParent && jumpState.pos[k] != null) el.scrollTop = jumpState.pos[k];
}
for (const [k, sel] of Object.entries(JUMP_LISTS)) {
  const el = $(sel);
  // a hidden list fires nothing; a re-render's momentary empty list is restored right after it
  el.addEventListener("scroll", () => {
    if (!el.offsetParent || el.dataset.restoring) return;
    jumpLoadPositions(); jumpState.pos[k] = el.scrollTop; jumpSavePositions();
  }, {passive: true});
}

// re-renders replace the list's children: put the list back where it was
for (const [fn, k] of [["renderLibrary", "assets"], ["renderRefs", "refs"], ["renderReview", "views"]]) {
  const orig = window[fn];
  window[fn] = function (...args) {
    jumpLoadPositions();
    const el = $(JUMP_LISTS[k]);
    if (el) el.dataset.restoring = "1";
    const r = orig.apply(this, args);
    jumpRestore(k);
    jumpOptions(k);
    jumpRemark(k);
    if (el) requestAnimationFrame(() => { delete el.dataset.restoring; });
    return r;
  };
}
// switching sub-tabs or tabs: the list that comes into view returns to its own place
{
  const sub0 = window.showSub, tab0 = window.showTab;
  window.showSub = function (sub) { const r = sub0(sub); if (JUMP_LISTS[sub]) jumpRestore(sub); return r; };
  window.showTab = function (tab) {
    const r = tab0(tab);
    if (tab === "views") jumpRestore("views");
    if (tab === "library") ["assets", "refs"].forEach(jumpRestore);
    return r;
  };
}

// --- jump to an asset ---------------------------------------------------------------------------

// the rows of a list: [{id, el, label}]
function jumpRows(k) {
  if (k === "assets") return [...document.querySelectorAll("#assets-list .asset-card[data-entry]")]
    .map(el => ({id: el.dataset.entry, el}));
  if (k === "refs") return [...document.querySelectorAll("#refs-list .unit[data-unit]")].map(el => ({id: el.dataset.unit, el}));
  if (k === "areas") return [...document.querySelectorAll("#area-table tr[data-asset]")].map(el => ({id: el.dataset.asset, el}));
  return [...document.querySelectorAll("#review-list .asset-review[data-asset]")].map(el => ({id: el.dataset.asset, el}));
}
const jumpLabel = id => (typeof assetName === "function" ? assetName(id) : id);
function jumpOptions(k) {
  const dl = $(`#${k}-jump-list`); if (!dl) return;
  dl.innerHTML = jumpRows(k).map(r => { const n = jumpLabel(r.id);
    return `<option value="${esc(n === r.id ? r.id : `${n} (${r.id})`)}"></option>`; }).join("");
}
// a typed or picked text -> the asset id it means
function jumpResolve(k, text) {
  const t = text.trim(); if (!t) return null;
  const m = t.match(/\(([a-z0-9-]+)\)$/);
  if (m) return m[1];
  const rows = jumpRows(k), low = t.toLowerCase();
  const all = typeof libEntries === "function" ? libEntries().map(e => e.id) : [];
  const ids = [...new Set([...rows.map(r => r.id), ...all])];
  return ids.find(id => id === low) || ids.find(id => jumpLabel(id).toLowerCase() === low)
    || ids.find(id => id.includes(low) || jumpLabel(id).toLowerCase().includes(low)) || null;
}
// the row for an asset in a list: itself, its kit modules (refs, views) or its entry (assets)
function jumpFind(k, id) {
  const rows = jumpRows(k);
  const ent = k === "assets" && typeof assetEntry === "function" ? assetEntry(id) || id : id;
  return rows.find(r => r.id === ent) || rows.find(r => r.id.startsWith(ent + "-"))
    || (k !== "assets" && typeof assetEntry === "function" ? rows.find(r => assetEntry(r.id) === assetEntry(id)) : null) || null;
}
function jumpMsg(k, html) { const m = $(`#${k}-jump-msg`); if (m) m.innerHTML = html; }

// the Show filter of each list: a jump to a row it hides switches it to "all"
const JUMP_FILTERS = {assets: "#lib-filter", refs: "#refs-filter", views: "#views-filter"};
function jumpSetFilter(sel, value) { const f = $(sel); f.value = value; f.dispatchEvent(new Event("change")); }

// The one highlight for every tab: the row is scrolled to the middle of its list, pulses briefly
// (not with reduced motion: style.css) and stays marked until a click elsewhere or the next jump.
function jumpHighlight(el, k = null, id = null) {
  jumpClear();
  const list = el.closest(".scroll-list");
  if (list) {
    const r = el.getBoundingClientRect(), lr = list.getBoundingClientRect();
    const pad = Math.max(4, (list.clientHeight - r.height) / 2);      // taller than the list: its top
    list.scrollTop += r.top - lr.top - pad;
  } else el.scrollIntoView({block: "center"});
  el.classList.add("jump-hit", "jump-pulse");
  setTimeout(() => el.classList.remove("jump-pulse"), 2000);
  jumpState.hit = {el, k, id};
}
function jumpClear() {
  const h = jumpState.hit; jumpState.hit = null;
  if (h) h.el.classList.remove("jump-hit", "jump-pulse");
}
// a re-render that rebuilt the marked row marks its new row (without the pulse)
function jumpRemark(k) {
  const h = jumpState.hit;
  if (!h || h.k !== k || h.el.isConnected) return;
  const row = jumpFind(k, h.id);
  if (row) { row.el.classList.add("jump-hit"); h.el = row.el; } else jumpState.hit = null;
}
document.addEventListener("pointerdown", e => { const h = jumpState.hit; if (h && !h.el.contains(e.target)) jumpClear(); }, true);

function jumpTo(k, id) {
  jumpMsg(k, "");
  let row = jumpFind(k, id), note = "";
  const fsel = JUMP_FILTERS[k], f = fsel && $(fsel);
  if (!row && f && f.value !== "all") {          // hidden by the Show filter? show all, and say so
    const was = f.value, wasText = (f.selectedOptions[0] || {}).textContent || was;
    jumpSetFilter(fsel, "all");
    row = jumpFind(k, id);
    if (row) note = `Show filter switched to All assets: ${esc(jumpLabel(id))} was hidden by "${esc(wasText.replace(/\s*\(\d+\)$/, ""))}".`;
    else jumpSetFilter(fsel, was);
  }
  if (!row) {
    jumpMsg(k, `${esc(jumpLabel(id))} has no row here${k === "refs" ? " (it isn't decided into the library plan)" : k === "areas" ? " (it isn't in this area)" : ""}.`);
    return false;
  }
  jumpMsg(k, note);
  jumpHighlight(row.el, k, id);
  return true;
}

// Jump to boxes (Areas builds its own after load): delegated
const jumpBox = t => t && t.classList && t.classList.contains("jump-box") ? t : null;
// Enter jumps every time; the change event a later blur sends for the same text doesn't jump again
const jumpGo = inp => { inp.dataset.done = inp.value; const k = inp.dataset.list, id = jumpResolve(k, inp.value);
  if (id) jumpTo(k, id); else if (inp.value.trim()) jumpMsg(k, "No asset matches."); };
document.addEventListener("input", e => { const inp = jumpBox(e.target); if (inp) delete inp.dataset.done; });
document.addEventListener("change", e => { const inp = jumpBox(e.target); if (inp && inp.dataset.done !== inp.value) jumpGo(inp); });
document.addEventListener("keydown", e => { const inp = jumpBox(e.target); if (inp && e.key === "Enter") { e.preventDefault(); jumpGo(inp); } });
document.addEventListener("focusin", e => { const inp = jumpBox(e.target); if (inp) jumpOptions(inp.dataset.list); });

// an asset's name in one Library sub-tab opens the same asset in the other
document.addEventListener("click", e => {
  const a = e.target.closest(".jump-link[data-jump]"); if (!a) return;
  e.preventDefault();
  const k = a.dataset.jump;
  showSub(k);
  requestAnimationFrame(() => jumpTo(k, a.dataset.asset));
});
