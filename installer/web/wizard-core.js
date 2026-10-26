/* Terraformer setup wizard: page flow, sounds, components and progress text, without the DOM
   (wizard.js draws it; tests/js/wizard_core.test.js drives this file in node with mock audio). */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.WizardCore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const PAGES = [
    { id: "welcome" },
    { id: "license", title: "License Agreement", sub: "Please read the following license agreement carefully." },
    { id: "hardware", title: "Hardware Profile", sub: "Setup detected this computer's graphics hardware. Change the profile if it is wrong." },
    { id: "components", title: "Choose Components", sub: "Select the models and assets you want to install. Required ones are always installed." },
    { id: "destination", title: "Choose Destination Location", sub: "Choose the folder where Setup will put models, environments and projects." },
    { id: "connections", title: "Connections", sub: "Tell Terraformer where ComfyUI, Blender and Unreal Engine are. Test each one." },
    { id: "ready", title: "Ready to Install", sub: "Setup has enough information to begin installing Terraformer." },
    { id: "installing", title: "Installing Terraformer", sub: "Please wait while Setup downloads and installs the selected components." },
    { id: "finish" },
  ];
  const IDS = PAGES.map(p => p.id);
  const SOUND_FILES = {
    open: "01-opening-door-vintage.mp3",
    tick: "02-selection-tick-soft.mp3",
    next: "03-next-glass-chime.mp3",
    done: "04-install-complete-shimmer.mp3",
  };
  const MUTE_KEY = "terraformer-installer-muted";
  const ACTIVE = ["running", "paused", "interrupted"];

  // --- sounds: preloaded, silent until the first user gesture, mute remembered ----------------------
  function createSounds({ makeAudio, storage, base = "assets/" }) {
    const audio = {};
    for (const [name, file] of Object.entries(SOUND_FILES)) {
      const a = makeAudio(base + file);
      a.preload = "auto";
      try { a.load && a.load(); } catch (e) { /* preloading is a hint */ }
      audio[name] = a;
    }
    let muted = false;
    try { muted = storage && storage.getItem(MUTE_KEY) === "1"; } catch (e) { /* private window */ }
    const s = {
      audio, unlocked: false, played: [],
      get muted() { return muted; },
      setMuted(m) {
        muted = !!m;
        try { storage && storage.setItem(MUTE_KEY, muted ? "1" : "0"); } catch (e) { /* not remembered */ }
      },
      unlock() { s.unlocked = true; },
      play(name) {
        const a = audio[name];
        if (!a || !s.unlocked || muted) return false;
        try { a.currentTime = 0; } catch (e) { /* not loaded yet */ }
        const p = a.play();
        if (p && p.catch) p.catch(() => {});
        s.played.push(name);
        return true;
      },
    };
    return s;
  }

  // --- formatting -------------------------------------------------------------------------------------
  function num(n, unit) { const v = n / unit; return (v >= 100 ? v.toFixed(0) : v.toFixed(1)).replace(/\.0$/, ""); }
  function size(n) {
    if (n == null) return "";
    if (n >= 1e8) return num(n, 1e9) + " GB";
    if (n >= 1e6) return Math.round(n / 1e6) + " MB";
    return Math.max(1, Math.round(n / 1e3)) + " KB";
  }
  function ofTotal(done, total) {            // "12.4 of 20 GB"
    if (total >= 1e8) return `${num(done, 1e9)} of ${num(total, 1e9)} GB`;
    return `${Math.round(done / 1e6)} of ${Math.max(1, Math.round(total / 1e6))} MB`;
  }
  function currentText(cur) {
    if (!cur || !cur.label) return "";
    if (cur.total) return `${cur.label}, ${ofTotal(Math.min(cur.done || 0, cur.total), cur.total)}`;
    return cur.label + "…";
  }
  function overallText(p) {
    if (!p || !p.overall) return "";
    const pct = Math.floor(100 * p.overall.fraction);
    const dl = p.download && p.download.total ? ` · ${ofTotal(p.download.done, p.download.total)} downloaded` : "";
    return `${pct}% complete${dl}`;
  }

  // --- components ----------------------------------------------------------------------------------------
  function isRequired(c, vlm) {
    return c.need === "required" || (c.need === "local.model" && c.select === vlm);
  }
  function isLocked(c, vlm) { return isRequired(c, vlm) || c.installed; }
  function groups(components) {
    const out = [];
    for (const c of components) {
      let g = out.find(x => x.name === c.group);
      if (!g) out.push(g = { name: c.group, items: [] });
      g.items.push(c);
    }
    return out;
  }
  function neededBytes(components, selected) {
    return components.filter(c => selected.has(c.id) && !c.installed).reduce((a, c) => a + (c.missing_bytes || 0), 0);
  }

  // --- the wizard -----------------------------------------------------------------------------------------
  function createWizard({ sounds, state = {}, catalog = null }) {
    const w = {
      index: 0, sounds, catalog,
      values: Object.assign({}, state.values || {}),
      secrets: {},
      license: !!state.license,
      selected: new Set(state.components || []),
      haveSelection: Array.isArray(state.components),
      openPlayed: false, lastStatus: (state.install || {}).status || "idle",
      install: state.install || { status: "idle", steps: [], errors: [] },
      installed: false, launch: true,

      get page() { return PAGES[w.index]; },
      setCatalog(cat) {
        w.catalog = cat;
        w.installed = !!cat.installed;
        for (const it of cat.items) {                // what is set, else what Setup found, else the recommendation
          if (w.values[it.id] === undefined && it.kind !== "secret" && it.kind !== "info") w.values[it.id] = it.value || it.detected || it.recommended || "";
          if (it.kind !== "secret") w.initial[it.id] = it.value || it.detected || "";
        }
        if (!w.haveSelection) { w.selected = new Set(cat.default_components); w.haveSelection = true; }
        w.syncRequired();
        w.applyComfyPlan();
      },
      initial: {}, planned: {},
      isRecommended(id) { return !!(w.catalog && (w.catalog.recommended_components || []).includes(id)); },
      // a local ComfyUI Setup installs: its address, folder, models folder and launch command follow the storage folder
      applyComfyPlan() {
        const plan = (w.catalog && w.catalog.comfyui && w.catalog.comfyui.planned) || {};
        const on = w.selected.has("comfyui");
        const storage = String(w.values.storage || "").replace(/\/+$/, "");
        for (const [k, tpl] of Object.entries(plan)) {
          const want = on ? tpl.replace("{storage}", storage) : w.initial[k] || "";
          const cur = w.values[k] || "";
          if (!cur || cur === w.initial[k] || cur === w.planned[k]) w.values[k] = want;
          w.planned[k] = on ? want : undefined;
        }
      },
      item(id) { return w.catalog ? w.catalog.items.find(i => i.id === id) : null; },
      items(page) { return w.catalog ? w.catalog.items.filter(i => i.wizard === page) : []; },
      shown(it) {
        if (!it.when) return true;
        const [k, v] = it.when.split("=");
        if (k === "backend") return (w.values.comfy_backend || "sdk") === v;
        return it.shown !== false;
      },
      vlm() { return w.values.vlm_model || "32b"; },
      syncRequired() {                                // the chosen vision model is required, the other one isn't
        if (!w.catalog) return;
        for (const c of w.catalog.components) if (isLocked(c, w.vlm())) w.selected.add(c.id);
        for (const c of w.catalog.components) {
          if (c.need === "local.model" && c.select !== w.vlm() && !c.installed && !w.touched?.has(c.id)) w.selected.delete(c.id);
        }
      },
      touched: new Set(),

      // flow
      skip(id) { return id === "license" && w.installed && w.license; },
      nextIndex(from) { let i = from + 1; while (i < IDS.length - 1 && w.skip(IDS[i])) i++; return i; },
      prevIndex(from) { let i = from - 1; while (i > 0 && w.skip(IDS[i])) i--; return Math.max(i, 0); },
      canBack() { return w.index > 0 && !["installing", "finish"].includes(w.page.id); },
      canNext() {
        const id = w.page.id;
        if (id === "license") return w.license;
        if (id === "hardware") return !!w.values.hardware_profile;
        if (id === "destination") return !!(w.values.storage || "").trim();
        if (id === "installing") return ["done", "failed"].includes(w.install.status);
        return true;
      },
      canCancel() { return w.page.id !== "finish"; },
      nextLabel() {
        const id = w.page.id;
        if (id === "ready") return "Install";
        if (id === "finish") return "Finish";
        return "Next >";
      },
      next() {
        if (!w.canNext() || w.page.id === "finish") return false;
        if (w.page.id === "welcome" && !w.openPlayed) { w.openPlayed = true; sounds.play("open"); }
        else sounds.play("next");
        w.index = w.nextIndex(w.index);
        return true;
      },
      back() {
        if (!w.canBack()) return false;
        w.index = w.prevIndex(w.index);
        return true;
      },
      go(id) { w.index = Math.max(0, IDS.indexOf(id)); },

      // selections: every checkbox, radio or list pick ticks
      select() { sounds.play("tick"); },
      setValue(id, v, tick = true) {
        w.values[id] = v;
        if (id === "vlm_model") w.syncRequired();
        if (id === "storage") w.applyComfyPlan();
        if (tick) w.select();
      },
      acceptLicense(on) { w.license = !!on; w.select(); },
      toggle(id) {
        const c = w.catalog.components.find(x => x.id === id);
        if (!c || isLocked(c, w.vlm())) return false;
        w.touched.add(id);
        if (w.selected.has(id)) w.selected.delete(id); else w.selected.add(id);
        if (id === "comfyui") w.applyComfyPlan();
        w.select();
        return true;
      },
      setLaunch(on) { w.launch = !!on; w.select(); },
      neededBytes() { return w.catalog ? neededBytes(w.catalog.components, w.selected) : 0; },

      // the install
      onProgress(p) {
        const before = w.lastStatus;
        w.install = p;
        w.lastStatus = p.status;
        if (p.status === "done" && before === "running") sounds.play("done");
        return p;
      },
      startPage(st) {                                 // where a reopened window starts
        const s = (st.install || {}).status;
        if (ACTIVE.includes(s)) return "installing";
        if ((s === "done" || s === "failed") && ["installing", "finish"].includes(st.page)) return st.page === "finish" ? "finish" : "installing";
        return IDS.includes(st.page) && !["installing", "finish"].includes(st.page) ? st.page : "welcome";
      },
      shouldResume(st) { return (st.install || {}).status === "interrupted"; },
      saved() {                                         // what the server keeps (never the API keys)
        return { page: w.page.id, values: w.values, components: [...w.selected], license: w.license };
      },
      summary() {
        const c = w.catalog || { items: [], components: [] };
        const label = (id) => { const it = w.item(id); const v = w.values[id]; return it && it.labels && it.labels[v] || v || "not set"; };
        const comps = c.components.filter(x => w.selected.has(x.id));
        const lines = [
          "Hardware profile:", "    " + label("hardware_profile"),
          "", "Destination folder:", "    " + (w.values.storage || ""),
          "", `Components (${comps.length}, ${size(w.neededBytes())} to download):`,
          ...comps.map(x => `    ${x.title}${x.installed ? " (installed)" : ""}`),
          "", "Connections:",
        ];
        for (const it of [...w.items("hardware"), ...w.items("connections")]) {
          if (it.id === "hardware_profile" || !w.shown(it)) continue;
          const v = it.kind === "secret" ? (w.secrets[it.id] ? "entered" : (it.has_value ? "already set" : "not set")) : label(it.id);
          lines.push(`    ${it.label}: ${v}`);
        }
        return lines.join("\n");
      },
    };
    if (catalog) w.setCatalog(catalog);
    return w;
  }

  return { PAGES, IDS, SOUND_FILES, MUTE_KEY, createSounds, createWizard, size, ofTotal, currentText, overallText,
           isRequired, isLocked, groups, neededBytes };
});
