// The setup wizard's page flow, sounds (mock audio) and progress text: installer/web/wizard-core.js.
// Run: node --test tests/js/wizard_core.test.js   (tests/test_installer_web.py runs it under pytest)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const C = require("../../installer/web/wizard-core.js");

function mockAudio() {
  const made = [];
  const make = (src) => {
    const a = { src, plays: 0, loaded: false, preload: "", currentTime: 5, load() { this.loaded = true; },
                play() { this.plays++; return Promise.resolve(); } };
    made.push(a);
    return a;
  };
  return { make, made };
}
function memStore(init = {}) {
  const m = Object.assign({}, init);
  return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, m };
}
const CATALOG = {
  installed: false,
  gpus: [{ index: 0, name: "GPU A", vram_gb: 32 }],
  items: [
    { id: "hardware_profile", wizard: "hardware", kind: "choice", choices: ["rocm-2gpu", "nvidia"], value: "", detected: "rocm-2gpu",
      labels: { "rocm-2gpu": "Ubuntu + ROCm, 2 GPUs", nvidia: "Ubuntu + NVIDIA (untested)" } },
    { id: "vlm_model", wizard: "hardware", kind: "choice", choices: ["32b", "8b"], value: "32b", label: "Vision model" },
    { id: "storage", wizard: "storage", kind: "path", value: "", detected: "/data/terraformer", label: "Storage folder" },
    { id: "comfy_backend", wizard: "connections", kind: "choice", choices: ["sdk", "httpx"], value: "sdk", label: "ComfyUI backend" },
    { id: "sdk_url", wizard: "connections", kind: "text", when: "backend=sdk", value: "http://127.0.0.1:8190", label: "Proxy" },
    { id: "comfy_api_key", wizard: "connections", kind: "secret", key: "secret:COMFY_API_KEY", when: "backend=sdk", label: "Key", has_value: false },
    { id: "blender", wizard: "connections", kind: "path", value: "", detected: "/usr/bin/blender", label: "Blender" },
  ],
  components: [
    { id: "flux", title: "FLUX", group: "ComfyUI image models", feature: "comfy", need: "required", size: 17e9, missing_bytes: 17e9, installed: false },
    { id: "vlm-32b", title: "VL 32B", group: "Vision model", feature: "vlm", need: "local.model", select: "32b", size: 24e9, missing_bytes: 24e9, installed: false },
    { id: "vlm-8b", title: "VL 8B", group: "Vision model", feature: "vlm", need: "local.model", select: "8b", size: 17e9, missing_bytes: 17e9, installed: false },
    { id: "trellis", title: "TRELLIS.2", group: "TRELLIS.2", feature: "trellis", need: "optional", size: 16e9, missing_bytes: 16e9, installed: false },
    { id: "grass", title: "Grass", group: "World dressing", feature: "dressing", need: "optional", size: 4e7, missing_bytes: 4e7, installed: false },
    { id: "old", title: "Installed one", group: "ComfyUI image models", feature: "comfy", need: "optional", size: 1e9, missing_bytes: 0, installed: true },
  ],
  default_components: ["flux", "vlm-32b", "grass", "old"],
  storage_free: 800e9,
};
function setup(state = {}, store = memStore()) {
  const audio = mockAudio();
  const sounds = C.createSounds({ makeAudio: audio.make, storage: store });
  const w = C.createWizard({ sounds, state, catalog: JSON.parse(JSON.stringify(CATALOG)) });
  return { w, sounds, audio, store };
}
const at = (w) => w.page.id;

test("the pages come in the fixed order with Back, Next and Install / Finish labels", () => {
  assert.deepEqual(C.IDS, ["welcome", "license", "hardware", "components", "destination", "connections", "ready", "installing", "finish"]);
  const { w } = setup();
  assert.equal(at(w), "welcome");
  assert.equal(w.canBack(), false);
  assert.ok(w.next());
  assert.equal(at(w), "license");
  assert.equal(w.canNext(), false, "the license must be accepted first");
  assert.equal(w.next(), false);
  w.acceptLicense(true);
  assert.ok(w.next());
  assert.equal(at(w), "hardware");
  assert.equal(w.values.hardware_profile, "rocm-2gpu", "the detected profile is preselected");
  w.next(); w.next(); w.next();
  assert.equal(at(w), "connections");
  assert.ok(w.back());
  assert.equal(at(w), "destination");
  w.next(); w.next();
  assert.equal(at(w), "ready");
  assert.equal(w.nextLabel(), "Install");
  w.next();
  assert.equal(at(w), "installing");
  assert.equal(w.canBack(), false);
  assert.equal(w.canNext(), false, "Next waits for the install");
  w.onProgress({ status: "running", overall: { fraction: 0.5 } });
  assert.equal(w.canNext(), false);
  w.onProgress({ status: "done", overall: { fraction: 1 } });
  assert.ok(w.next());
  assert.equal(at(w), "finish");
  assert.equal(w.nextLabel(), "Finish");
  assert.equal(w.canCancel(), false);
  assert.equal(w.canBack(), false);
});

test("an empty destination blocks Next", () => {
  const { w } = setup({ license: true });
  w.go("destination");
  w.values.storage = "  ";
  assert.equal(w.canNext(), false);
  w.values.storage = "/data/tf";
  assert.equal(w.canNext(), true);
});

test("sounds: door on the first Next, chime on later ones, tick on picks, shimmer when installing finishes", () => {
  const { w, sounds, audio } = setup();
  assert.deepEqual(audio.made.map(a => a.src), ["assets/01-opening-door-vintage.mp3", "assets/02-selection-tick-soft.mp3",
    "assets/03-next-glass-chime.mp3", "assets/04-install-complete-shimmer.mp3"]);
  assert.ok(audio.made.every(a => a.preload === "auto" && a.loaded), "all four preloaded");
  sounds.unlock();                                    // the user's first click
  w.next();
  assert.deepEqual(sounds.played, ["open"]);
  assert.equal(audio.made[0].currentTime, 0, "played from the start");
  w.acceptLicense(true);
  assert.deepEqual(sounds.played, ["open", "tick"]);
  w.next();
  w.setValue("hardware_profile", "nvidia");           // a radio
  w.next();
  w.toggle("trellis");                                // a checkbox
  w.select();                                         // a list item
  assert.equal(w.toggle("flux"), false, "a required component can't be unticked");
  w.back();
  w.next();                                           // Next from a later page: the chime, not the door
  w.go("welcome");
  w.next();                                           // Welcome again: the door played once already
  assert.deepEqual(sounds.played, ["open", "tick", "next", "tick", "next", "tick", "tick", "next", "next"]);
  w.go("ready");
  w.next();                                           // Install counts as a Next
  assert.equal(sounds.played.at(-1), "next");
  w.onProgress({ status: "running" });
  w.onProgress({ status: "running" });
  assert.equal(sounds.played.at(-1), "next");
  w.onProgress({ status: "done" });
  assert.equal(sounds.played.at(-1), "done");
  w.onProgress({ status: "done" });
  assert.equal(sounds.played.filter(s => s === "done").length, 1, "the shimmer plays once");
  w.next();                                           // Next on Installing: a later Next, the chime
  assert.equal(at(w), "finish");
  assert.ok(!w.next(), "Finish isn't a Next");
  assert.equal(sounds.played.filter(s => s === "next").length, 6);
  assert.equal(sounds.played.filter(s => s === "open").length, 1);
});

test("sounds stay silent before a user gesture (autoplay rules) and when muted; mute is remembered", () => {
  const store = memStore();
  const { w, sounds, audio } = setup({}, store);
  w.next();
  assert.deepEqual(sounds.played, []);
  assert.equal(audio.made[0].plays, 0);
  sounds.unlock();
  sounds.setMuted(true);
  w.next();
  w.select();
  assert.deepEqual(sounds.played, []);
  assert.equal(store.m[C.MUTE_KEY], "1");
  const again = setup({}, store);                     // the page opened again
  assert.equal(again.sounds.muted, true);
  again.sounds.unlock();
  again.w.select();
  assert.deepEqual(again.sounds.played, []);
  again.sounds.setMuted(false);
  again.w.select();
  assert.deepEqual(again.sounds.played, ["tick"]);
  const broken = C.createSounds({ makeAudio: mockAudio().make, storage: { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } } });
  assert.equal(broken.muted, false, "storage that throws: sound on, nothing remembered");
  broken.setMuted(true);
  assert.equal(broken.muted, true);
});

test("the shimmer doesn't play for an install that was already done when the page opened", () => {
  const { w, sounds } = setup({ install: { status: "done" }, page: "finish" });
  sounds.unlock();
  w.onProgress({ status: "done" });
  assert.deepEqual(sounds.played, []);
});

test("components: required and the chosen vision model are locked; sizes add up", () => {
  const { w } = setup();
  assert.deepEqual([...w.selected].sort(), ["flux", "grass", "old", "vlm-32b"]);
  assert.equal(w.neededBytes(), 17e9 + 24e9 + 4e7, "installed ones don't count");
  assert.equal(w.toggle("vlm-32b"), false);
  assert.equal(w.toggle("old"), false, "no uninstalling here");
  assert.ok(w.toggle("trellis"));
  assert.equal(w.neededBytes(), 17e9 + 24e9 + 4e7 + 16e9);
  w.setValue("vlm_model", "8b");                      // the hardware page's vision model
  assert.ok(w.selected.has("vlm-8b") && !w.selected.has("vlm-32b"));
  assert.ok(C.isLocked(CATALOG.components[2], w.vlm()));
  assert.deepEqual(C.groups(CATALOG.components).map(g => g.name), ["ComfyUI image models", "Vision model", "TRELLIS.2", "World dressing"]);
});

test("connections follow the backend; secrets never go into what is saved", () => {
  const { w } = setup();
  const shown = () => w.items("connections").filter(w.shown).map(i => i.id);
  assert.deepEqual(shown(), ["comfy_backend", "sdk_url", "comfy_api_key", "blender"]);
  w.setValue("comfy_backend", "httpx");
  assert.deepEqual(shown(), ["comfy_backend", "blender"]);
  assert.equal(w.values.blender, "/usr/bin/blender", "detected values prefill");
  w.secrets.comfy_api_key = "secret-value-123";
  const saved = JSON.stringify(w.saved());
  assert.ok(!saved.includes("secret-value-123"));
  assert.match(w.summary(), /Comfy|ComfyUI backend: httpx/);
});

test("progress text: current item and size, overall", () => {
  assert.equal(C.currentText({ label: "Downloading Qwen3-VL 32B", done: 12.4e9, total: 20e9 }), "Downloading Qwen3-VL 32B, 12.4 of 20 GB");
  assert.equal(C.currentText({ label: "Downloading Grass", done: 12e6, total: 39.9e6 }), "Downloading Grass, 12 of 40 MB");
  assert.equal(C.currentText({ label: "Installing the app", done: null, total: null }), "Installing the app…");
  assert.equal(C.currentText(null), "");
  assert.equal(C.overallText({ overall: { fraction: 0.4237 }, download: { done: 21.3e9, total: 50.6e9 } }),
    "42% complete · 21.3 of 50.6 GB downloaded");
  assert.equal(C.size(24411628992), "24.4 GB");
  assert.equal(C.size(129063232), "0.1 GB");
  assert.equal(C.size(39900302), "40 MB");
  assert.equal(C.size(1966425), "2 MB");
});

test("progress from a stream of mocked download events", () => {
  const { w, sounds } = setup();
  sounds.unlock();
  const total = 30e9;
  const stream = [0, 5e9, 12.4e9, 20e9].map(d => ({ status: "running", current: { label: "Downloading FLUX", done: Math.min(d, 20e9), total: 20e9 },
    download: { done: d, total }, overall: { fraction: d / total } }));
  const texts = stream.map(p => (w.onProgress(p), C.currentText(w.install.current)));
  assert.deepEqual(texts, ["Downloading FLUX, 0 of 20 GB", "Downloading FLUX, 5 of 20 GB", "Downloading FLUX, 12.4 of 20 GB", "Downloading FLUX, 20 of 20 GB"]);
  assert.equal(C.overallText(w.install), "66% complete · 20 of 30 GB downloaded");
  w.onProgress({ status: "done", overall: { fraction: 1 }, download: { done: total, total } });
  assert.equal(sounds.played.at(-1), "done");
});

test("resume: a reopened window goes back to its page or to Installing", () => {
  const { w } = setup({ license: true });
  assert.equal(w.startPage({ page: "components", install: { status: "idle" } }), "components");
  assert.equal(w.startPage({ page: "connections", install: { status: "running" } }), "installing");
  assert.equal(w.startPage({ page: "ready", install: { status: "paused" } }), "installing");
  assert.equal(w.startPage({ page: "installing", install: { status: "interrupted" } }), "installing");
  assert.equal(w.startPage({ page: "installing", install: { status: "done" } }), "installing");
  assert.equal(w.startPage({ page: "finish", install: { status: "done" } }), "finish");
  assert.equal(w.startPage({ page: "installing", install: { status: "idle" } }), "welcome");
  assert.equal(w.startPage({ page: "nonsense", install: {} }), "welcome");
  assert.ok(w.shouldResume({ install: { status: "interrupted" } }), "the server stopped mid-install: resume by itself");
  assert.ok(!w.shouldResume({ install: { status: "paused" } }), "the user paused: wait for Resume");
  const saved = setup({ values: { storage: "/x" }, components: ["flux", "trellis"], license: true }).w;
  assert.equal(saved.values.storage, "/x", "saved values win over detected ones");
  assert.ok(saved.selected.has("trellis") && saved.selected.has("vlm-32b"));
});

test("ap setup on an installed system skips the accepted license", () => {
  const audio = mockAudio();
  const sounds = C.createSounds({ makeAudio: audio.make, storage: memStore() });
  const w = C.createWizard({ sounds, state: { license: true }, catalog: Object.assign({}, CATALOG, { installed: true }) });
  w.next();
  assert.equal(at(w), "hardware");
  w.back();
  assert.equal(at(w), "welcome");
  const fresh = setup({ license: true }).w;            // first install: the license page still shows
  fresh.next();
  assert.equal(at(fresh), "license");
});

test("recommendations: the best choice for this machine is tagged and preselected", () => {
  const cat = JSON.parse(JSON.stringify(CATALOG));
  const vlm = cat.items.find(i => i.id === "vlm_model");
  vlm.value = ""; vlm.recommended = "8b";                        // a 24 GB vision GPU: the 32B doesn't fit
  cat.items[0].recommended = "rocm-2gpu";
  cat.recommended_components = ["vlm-8b"];
  const w = C.createWizard({ sounds: C.createSounds({ makeAudio: mockAudio().make, storage: memStore() }), state: {}, catalog: cat });
  assert.equal(w.values.vlm_model, "8b", "preselected");
  assert.equal(w.values.hardware_profile, "rocm-2gpu");
  assert.ok(w.isRecommended("vlm-8b") && !w.isRecommended("vlm-32b"));
  assert.ok(w.selected.has("vlm-8b") && !w.selected.has("vlm-32b"), "the recommended vision model is the one installed");
});

test("no ComfyUI found: Install ComfyUI is checked and its settings follow the storage folder", () => {
  const cat = JSON.parse(JSON.stringify(CATALOG));
  cat.items.push({ id: "comfy_url", wizard: "connections", kind: "text", value: "http://127.0.0.1:8188", label: "ComfyUI address" },
                 { id: "comfy_cwd", wizard: "advanced", kind: "path", value: "~/ComfyUI", label: "ComfyUI folder" },
                 { id: "comfy_models_dir", wizard: "connections", kind: "path", value: "", label: "Models folder" });
  cat.components.unshift({ id: "comfyui", title: "Install ComfyUI (local)", group: "ComfyUI", feature: "tool", need: "optional",
                           size: 6e9, missing_bytes: 6e9, installed: false, tool: true });
  cat.default_components = [...cat.default_components, "comfyui"];
  cat.recommended_components = ["comfyui"];
  cat.comfyui = { found: false, planned: { comfy_url: "http://127.0.0.1:8188", comfy_cwd: "{storage}/ComfyUI",
                                           comfy_models_dir: "{storage}/ComfyUI/models" } };
  const { sounds } = setup();
  const w = C.createWizard({ sounds, state: {}, catalog: cat });
  assert.ok(w.selected.has("comfyui") && w.isRecommended("comfyui"));
  assert.equal(w.values.comfy_cwd, "/data/terraformer/ComfyUI", "filled in, nothing typed");
  assert.equal(w.values.comfy_models_dir, "/data/terraformer/ComfyUI/models");
  w.setValue("storage", "/big/tf/", false);
  assert.equal(w.values.comfy_models_dir, "/big/tf/ComfyUI/models", "follows the storage folder");
  w.values.comfy_cwd = "/my/own/ComfyUI";                         // typed by the user: kept
  w.setValue("storage", "/other", false);
  assert.equal(w.values.comfy_cwd, "/my/own/ComfyUI");
  assert.equal(w.neededBytes(), 17e9 + 24e9 + 4e7 + 6e9, "its download counts");
  w.toggle("comfyui");                                            // unticked: back to what was there
  assert.equal(w.values.comfy_models_dir, "");
  assert.equal(w.values.comfy_cwd, "/my/own/ComfyUI");
});
