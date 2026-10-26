/* Terraformer setup wizard: draws the pages (logic in wizard-core.js) and talks to the bootstrap
   server (asset_pipeline/installer.py). */
(function () {
  "use strict";
  const C = window.WizardCore;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  let store = null;
  try { store = window.localStorage; } catch (e) { store = null; }

  const api = {
    get: (p) => fetch(p, { cache: "no-store" }).then(r => r.json()),
    post: (p, body) => fetch(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }).then(r => r.json()),
  };
  const sounds = C.createSounds({ makeAudio: (src) => new Audio(src), storage: store });
  let W = null, S = null, poll = null, slides = null, assets = [];
  const results = {};                                  // item id -> last Test result
  let showAdvanced = false;

  // --- chrome ---------------------------------------------------------------------------------------
  function fit() {
    const s = Math.min(1, (window.innerWidth - 16) / 680, (window.innerHeight - 16) / 510);
    document.documentElement.style.setProperty("--fit", String(Math.max(s, 0.3)));
  }
  function fallbackImages(root) {
    root.querySelectorAll("img[data-fallback]").forEach(img => {
      img.addEventListener("error", () => {
        if (img.dataset.fallback && img.src.indexOf(img.dataset.fallback) < 0) img.src = img.dataset.fallback;
      }, { once: true });
    });
  }
  function speaker() {
    const b = $("speaker");
    b.classList.toggle("muted", sounds.muted);
    b.title = sounds.muted ? "Sound off (click to unmute)" : "Sound on (click to mute)";
    b.setAttribute("aria-pressed", String(sounds.muted));
  }
  function save() { api.post("api/state", W.saved()).catch(() => {}); }

  function chrome() {
    const p = W.page;
    const head = !!p.title;
    $("header").hidden = !head;
    if (head) { $("h-title").textContent = p.title; $("h-sub").textContent = p.sub; }
    const back = $("btn-back"), next = $("btn-next"), cancel = $("btn-cancel");
    back.disabled = !W.canBack();
    next.textContent = W.nextLabel();
    next.disabled = !W.canNext();
    next.classList.toggle("default", !next.disabled);
    cancel.disabled = !W.canCancel();
    back.style.visibility = p.id === "finish" ? "hidden" : "";
  }

  // --- pages ------------------------------------------------------------------------------------------
  function sideBanner() {
    const name = assets.find(a => /^terraformer-installer-sidebanner-.*\.png$/i.test(a));
    return `<div class="banner">${name ? `<img src="assets/${esc(name)}" alt="">` : `<div class="fallback"></div>`}</div>`;
  }

  const PAGE = {
    welcome() {
      const again = W.installed;
      return `<div class="split">${sideBanner()}<div class="text">
        <h1>Welcome to the Terraformer Setup Wizard</h1>
        ${again ? `<p>Terraformer is already installed. This wizard lets you add components, change your hardware profile,
          storage folder and connections. Finished steps are skipped.</p>`
        : `<p>The Setup Wizard will install Terraformer on your computer: the app, the open-weights models it runs
          locally, and their environments.</p><p>Terraformer turns concept images into reviewed 3D assets and whole
          scenes, with ComfyUI, Blender and Unreal Engine.</p>`}
        <p>To continue, click Next.</p></div></div>`;
    },
    license() {
      return `<div class="pad"><p>Press the Page Down key or scroll to see the rest of the agreement.</p>
        <textarea class="sunken license-text" readonly>${esc(S.license_text)}</textarea>
        <p style="margin-top:10px">Do you accept all the terms of the license agreement? You must accept it to install
        Terraformer. The models keep their own licenses (shown on the Components page).</p>
        <label class="choice"><input type="radio" name="lic" value="1"${W.license ? " checked" : ""}> I accept the terms of the license agreement</label>
        <label class="choice"><input type="radio" name="lic" value="0"${W.license ? "" : " checked"}> I do not accept the terms of the license agreement</label></div>`;
    },
    hardware() {
      const g = W.catalog.gpus || [];
      const det = g.length ? `${g.length} GPU${g.length > 1 ? "s" : ""}: ${g.map(x => `${esc(x.name)} (${Math.round(x.vram_gb)} GB)`).join(", ")}`
        : "no GPU found (no rocm-smi or nvidia-smi answered)";
      return `<div class="pad"><p class="detected">Setup found ${det}.</p>
        ${W.items("hardware").map(it => rowHtml(it)).join("")}</div>`;
    },
    components() {
      const vlm = W.vlm();
      const rows = C.groups(W.catalog.components).map(g => `<div class="grp">${esc(g.name)}</div>` + g.items.map(c => {
        const locked = C.isLocked(c, vlm), on = W.selected.has(c.id);
        const tag = c.installed ? "installed" : C.isRequired(c, vlm) ? "required" : c.need === "experimental" ? "experimental" : "";
        const rec = W.isRecommended(c.id) && !c.installed ? `<span class="tag rec">Recommended</span>` : "";
        return `<div class="it" data-id="${esc(c.id)}"><input type="checkbox" data-id="${esc(c.id)}"${on ? " checked" : ""}${locked ? " disabled" : ""}>
          <span class="name">${esc(c.title)}${tag ? `<span class="tag">${tag}</span>` : ""}${rec}</span><span class="sz">${C.size(c.size)}</span></div>`;
      }).join("")).join("");
      return `<div class="pad"><div class="sunken listbox" id="complist">${rows}</div>
        <fieldset><legend>Description</legend><div class="desc" id="desc">Point at a component to see what it is for.</div></fieldset>
        <div class="space" id="space"></div></div>`;
    },
    destination() {
      return `<div class="pad"><p>Setup will put the models, model environments, projects and outputs in this folder.
        To use another folder, type its full path and click Test.</p>
        <fieldset><legend>Destination Folder</legend>${W.items("storage").map(it => rowHtml(it, true)).join("")}</fieldset>
        <div class="space" id="space"></div></div>`;
    },
    connections() {
      const adv = W.items("advanced").filter(W.shown);
      const cf = W.catalog.comfyui || {};
      const local = W.selected.has("comfyui") && !cf.found;
      return `<div class="pad"><p class="note127">127.0.0.1 is this computer: Terraformer talks to ComfyUI here, and nothing is sent online.${
          local ? " No ComfyUI was found, so Setup installs one and starts it (Components page)." : cf.where ? ` ComfyUI found ${esc(cf.where)}.` : ""}</p>
        ${W.items("connections").filter(W.shown).map(it => rowHtml(it)).join("")}
        ${adv.length ? `<button class="btn" id="adv">${showAdvanced ? "Hide advanced" : "Advanced..."}</button>` : ""}
        ${adv.length && showAdvanced ? `<fieldset><legend>Advanced</legend>${adv.map(it => rowHtml(it)).join("")}</fieldset>` : ""}</div>`;
    },
    ready() {
      return `<div class="pad"><p>Click Install to begin. To review or change any settings, click Back. Click Cancel to exit
        Setup; your choices are kept for next time.</p>
        <p>Current settings:</p><textarea class="sunken" readonly style="height:220px">${esc(W.summary())}</textarea></div>`;
    },
    installing() {
      return `<div class="pad" style="padding-top:10px"><div class="strip sunken" id="strip"></div>
        <div class="status" id="status"></div><div class="sunken progress"><div class="fill" id="fill"></div></div>
        <div class="overall"><span id="overall"></span><span id="inst-actions"></span></div>
        <div class="errors" id="errors"></div></div>`;
    },
    finish() {
      const bad = W.install.status === "failed";
      return `<div class="split">${sideBanner()}<div class="text">
        <h1>Completing the Terraformer Setup Wizard</h1>
        ${bad ? `<p>Setup finished, but some steps need attention:</p><p>${(W.install.errors || []).map(e => esc(e.message)).join("<br>")}</p>
          <p>Fix them, then run <b>ap setup</b> to finish; everything already done is kept.</p>`
        : `<p>Setup has finished installing Terraformer on your computer.</p>`}
        <label class="choice"><input type="checkbox" id="launch"${W.launch ? " checked" : ""}> Launch Terraformer</label>
        <p class="note">Closing the browser doesn't stop jobs; closing the Terraformer window does.</p>
        <p>Click Finish to close Setup.</p></div></div>`;
    },
  };

  function inputHtml(it) {
    const v = W.values[it.id] == null ? "" : W.values[it.id];
    const id = "f-" + it.id;
    if (it.kind === "choice") {
      const notes = it.notes || {};
      const stacked = Object.keys(notes).length || it.choices.some(c => ((it.labels || {})[c] || c).length > 28);
      return `<div class="${stacked ? "" : "radios"}">${it.choices.map(c => {
        const tested = it.tested ? it.tested[c] : true;
        const label = (it.labels || {})[c] || c;
        const note = notes[c] ? `<span class="note">${esc(notes[c].split(/(?<=\.)\s/)[0])}</span>` : "";
        return `<label class="choice" title="${esc(notes[c] || "")}"><input type="radio" name="${id}" data-id="${esc(it.id)}" value="${esc(c)}"${c === v ? " checked" : ""}>
          <span>${esc(label)}${tested === false && !/untested|beta/i.test(label) ? `<span class="tag">untested</span>` : ""}${
            it.recommended === c ? `<span class="tag rec">Recommended</span>` : ""}${note}</span></label>`;
      }).join("")}</div>`;
    }
    if (it.kind === "gpu") {
      const g = W.catalog.gpus || [];
      if (!g.length) return `<input class="sunken field" id="${id}" data-id="${esc(it.id)}" value="${esc(v)}">`;
      return `<select class="sunken" id="${id}" data-id="${esc(it.id)}">${g.map(x => `<option value="${x.index}"${String(x.index) === String(v) ? " selected" : ""}>GPU ${x.index}: ${esc(x.name)} (${Math.round(x.vram_gb)} GB)</option>`).join("")}</select>`;
    }
    if (it.kind === "secret") {
      const ph = it.has_value ? "(set; leave empty to keep it)" : (it.optional ? "(optional)" : "");
      return `<input class="sunken field" type="password" autocomplete="off" id="${id}" data-id="${esc(it.id)}" data-secret="1" placeholder="${esc(ph)}" value="${esc(W.secrets[it.id] || "")}">`;
    }
    return `<input class="sunken field" id="${id}" data-id="${esc(it.id)}" value="${esc(v)}" spellcheck="false" placeholder="${it.optional ? "(optional)" : ""}">`;
  }
  function resultHtml(id) {
    const r = results[id];
    if (!r) return "";
    return `<div class="result ${r.ok ? (r.warn ? "warn" : "ok") : "bad"}">${esc(r.message || r.error || "")}</div>`;
  }
  function rowHtml(it, withHelp) {
    const test = it.kind !== "info" ? `<button class="btn small" data-test="${esc(it.id)}">Test</button>` : "";
    const radio = it.kind === "choice";
    return `<div class="row" data-row="${esc(it.id)}"><span class="lbl">${esc(it.label)}${it.optional ? " (optional)" : ""}:</span>
      <div class="line">${inputHtml(it)}${radio ? "" : test}</div>
      ${withHelp ? `<div class="help">${esc(it.help)}</div>` : ""}
      ${it.kind === "secret" && it.optional ? `<div class="help">Skip for now — you can add these later in Settings.</div>` : ""}
      <div data-result="${esc(it.id)}">${resultHtml(it.id)}</div></div>`;
  }

  // --- drawing and events --------------------------------------------------------------------------------
  function render() {
    const body = $("body");
    stopSlides();
    body.innerHTML = PAGE[W.page.id]();
    fallbackImages(body);
    chrome();
    if (W.page.id === "components") { space(); bindList(); }
    if (W.page.id === "destination") space();
    if (W.page.id === "installing") { strip(); drawProgress(); startPoll(); }
    document.body.dataset.page = W.page.id;
  }

  function space() {
    const el = $("space");
    if (!el) return;
    const need = W.neededBytes();
    const draw = (free) => {
      const short = free != null && need + 2e9 > free;
      el.innerHTML = `<span>Space required:</span><span>${C.size(need) || "0 MB"}</span>
        <span>Space available:</span><span class="${short ? "short" : ""}">${free == null ? "unknown" : C.size(free)}${short ? " (not enough)" : ""}</span>`;
    };
    draw(W.catalog.storage_free);
    api.post("api/disk", { path: W.values.storage }).then(r => draw(r.free)).catch(() => {});
  }

  function bindList() {
    const list = $("complist");
    const describe = (id) => {
      const c = W.catalog.components.find(x => x.id === id);
      if (c) $("desc").innerHTML = `${esc(c.used_by)}.<br>License: ${esc(c.license)}${c.vram_gb ? ` · needs about ${c.vram_gb} GB of VRAM` : ""}${c.gated ? " · gated: needs a Hugging Face token" : ""}`;
    };
    list.addEventListener("mouseover", (e) => { const it = e.target.closest(".it"); if (it) describe(it.dataset.id); });
    list.addEventListener("click", (e) => {
      const it = e.target.closest(".it");
      if (!it) return;
      list.querySelectorAll(".it.sel").forEach(x => x.classList.remove("sel"));
      it.classList.add("sel");
      describe(it.dataset.id);
      if (e.target.matches("input[type=checkbox]")) {
        e.preventDefault();
        if (W.toggle(it.dataset.id)) setTimeout(() => { e.target.checked = W.selected.has(it.dataset.id); space(); save(); });
      } else W.select();                                 // a list item picked
    });
  }

  function testItem(id) {
    const el = document.querySelector(`[data-result="${id}"]`);
    if (el) el.innerHTML = `<div class="result">Testing…</div>`;
    const it = W.item(id);
    const value = it && it.kind === "secret" ? (W.secrets[id] || "") : W.values[id];
    api.post("api/test", { id, value, values: W.values }).then(r => {
      results[id] = r;
      const now = document.querySelector(`[data-result="${id}"]`);
      if (now) now.innerHTML = resultHtml(id);
    }).catch(() => {});
  }

  function onChange(e) {
    const t = e.target;
    if (t.name === "lic") { W.acceptLicense(t.value === "1"); chrome(); save(); return; }
    if (t.id === "launch") { W.setLaunch(t.checked); return; }
    const id = t.dataset && t.dataset.id;
    if (!id || W.page.id === "components") return;
    if (t.dataset.secret) { W.secrets[id] = t.value; return; }
    const tick = t.type === "radio" || t.tagName === "SELECT";
    W.setValue(id, t.value, tick);
    delete results[id];
    if (tick && id === "comfy_backend") render();      // the proxy rows follow the backend
    else chrome();
    if (W.page.id === "destination") space();
    save();
  }
  function onInput(e) {
    const t = e.target;
    if (t.tagName !== "INPUT" || t.type === "radio" || t.type === "checkbox") return;
    const id = t.dataset.id;
    if (!id) return;
    if (t.dataset.secret) W.secrets[id] = t.value; else W.values[id] = t.value;
    chrome();
  }

  // --- installing ------------------------------------------------------------------------------------------
  function strip() {
    const box = $("strip");
    const video = assets.includes("concept-banner-preview.mp4");
    const slideshow = () => {
      box.innerHTML = "";
      api.get("assets/sequence.json").then(seq => {
        const imgs = (seq.images || []).filter(n => assets.includes(n));
        if (!imgs.length) { box.innerHTML = `<div class="fallback"></div>`; return; }
        box.innerHTML = imgs.map((n, i) => `<img src="assets/${esc(n)}" alt=""${i === 0 ? ` class="on"` : ""} style="transition-duration:${seq.transition_seconds || 1}s">`).join("");
        const els = [...box.querySelectorAll("img")];
        let i = 0;
        slides = setInterval(() => { els[i].classList.remove("on"); i = (i + 1) % els.length; els[i].classList.add("on"); },
          1000 * (seq.image_seconds || 15));
      }).catch(() => { box.innerHTML = `<div class="fallback"></div>`; });
    };
    if (!video) return slideshow();
    box.innerHTML = `<video muted loop autoplay playsinline preload="auto" src="assets/concept-banner-preview.mp4"></video>`;
    const v = box.querySelector("video");
    v.muted = true;
    v.addEventListener("error", slideshow, { once: true });
    const p = v.play();
    if (p && p.catch) p.catch((err) => { if (err && err.name !== "AbortError") slideshow(); });
  }
  function stopSlides() { if (slides) { clearInterval(slides); slides = null; } }

  function drawProgress() {
    const p = W.install;
    if (!$("fill")) return;
    $("fill").style.width = (100 * ((p.overall || {}).fraction || 0)).toFixed(1) + "%";
    const st = p.status;
    $("status").textContent = st === "done" ? "Installation complete." : st === "paused" ? "Installation paused."
      : st === "failed" ? "Some steps need attention (below)." : (C.currentText(p.current) || "Preparing…");
    $("overall").textContent = C.overallText(p);
    const act = $("inst-actions");
    act.innerHTML = st === "paused" || st === "interrupted" ? `<button class="btn small" id="resume">Resume</button>`
      : st === "failed" ? `<button class="btn small" id="resume">Retry</button>` : "";
    $("errors").innerHTML = (p.errors || []).map(e => `<div>${esc(e.message)}${e.fix ? ` <span class="fix">${esc(e.fix)}</span>` : ""}</div>`).join("");
    chrome();
  }
  function startPoll() {
    if (poll) return;
    const tick = () => api.get("api/progress").then(p => {
      W.onProgress(p);
      drawProgress();
      if (["done", "failed", "paused"].includes(p.status)) { clearInterval(poll); poll = null; }
    }).catch(() => {});
    poll = setInterval(tick, 700);
    tick();
  }
  function install() {
    const secrets = {};
    for (const [id, v] of Object.entries(W.secrets)) {
      const it = W.item(id);
      if (it && v) secrets[it.key.replace(/^secret:/, "")] = v;
    }
    return api.post("api/install", { secrets }).then(p => { W.onProgress(p); drawProgress(); startPoll(); });
  }

  // --- buttons ------------------------------------------------------------------------------------------
  function onNext() {
    if (W.page.id === "finish") return finish(W.launch);
    const from = W.page.id;
    if (!W.next()) return;
    save();
    render();
    if (from === "ready") install();
  }
  function onBack() { if (W.back()) { save(); render(); } }
  function closed(msg, url) {
    stopSlides();
    $("body").innerHTML = `<div class="pad"><p style="margin-top:40px">${esc(msg)}</p></div>`;
    ["btn-back", "btn-next", "btn-cancel"].forEach(b => { $(b).disabled = true; });
    $("header").hidden = true;
    if (url) waitFor(url);
  }
  function waitFor(url, tries = 0) {               // the app's own server: open it once it answers
    fetch(url, { mode: "no-cors", cache: "no-store" }).then(() => { location.href = url; })
      .catch(() => { if (tries < 60) setTimeout(() => waitFor(url, tries + 1), 1000); });
  }
  function finish(launch) {
    api.post("api/finish", { launch }).then(r => {
      if (r.url && r.window) return closed("Terraformer is starting in its own window. You can close this one.");
      closed(r.url ? "Starting Terraformer…" : "Setup has finished. You can close this window.", r.url);
    }).catch(() => closed("Setup has finished. You can close this window."));
  }
  function onCancel() {
    if (W.page.id === "installing" && W.install.status === "running") {
      if (!confirm("Pause the installation? Downloads resume where they stopped when you run Setup again.")) return;
      api.post("api/cancel").then(p => { W.onProgress(p); drawProgress(); });
      return;
    }
    if (!confirm("Are you sure you want to cancel Terraformer Setup? Your choices are kept for next time.")) return;
    api.post("api/finish", { launch: false }).finally(() => closed("Setup was cancelled. Run it again to continue."));
  }

  function boot() {
    fit();
    window.addEventListener("resize", fit);
    fallbackImages(document);
    const unlock = () => sounds.unlock();
    document.addEventListener("pointerdown", unlock, true);
    document.addEventListener("keydown", unlock, true);
    $("speaker").addEventListener("click", () => { sounds.setMuted(!sounds.muted); speaker(); });
    speaker();
    $("btn-next").addEventListener("click", onNext);
    $("btn-back").addEventListener("click", onBack);
    $("btn-cancel").addEventListener("click", onCancel);
    $("tb-close").addEventListener("click", onCancel);
    const body = $("body");
    body.addEventListener("change", onChange);
    body.addEventListener("input", onInput);
    body.addEventListener("click", (e) => {
      const t = e.target.closest("[data-test]");
      if (t) return testItem(t.dataset.test);
      if (e.target.id === "resume") install();
      if (e.target.id === "adv") { showAdvanced = !showAdvanced; render(); }
    });
    Promise.all([api.get("api/state"), api.get("api/catalog")]).then(([st, cat]) => {
      S = st;
      assets = st.assets || [];
      W = C.createWizard({ sounds, state: st });
      if (st.profile_hint && !W.values.hardware_profile) W.values.hardware_profile = st.profile_hint;
      W.setCatalog(cat);
      W.go(W.startPage(st));
      window.wizard = W;                                  // for the screenshot script and debugging
      render();
      if (W.shouldResume(st)) install();
    });
  }
  window.wizardUI = { render, drawProgress, strip };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
