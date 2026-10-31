// Layout tab: the city in 3D (#city3d), drawn in the browser from the plan (GET .../site/city3d,
// city_view.py) with three.js, vendored under static/vendor/three and loaded on first use (no CDN).
// Buildings are one instanced box mesh per type; roads, rivers and parks are merged meshes; the
// terrain is the plan's heights, decimated. It redraws by itself after Roll a city, Update city plan
// and Re-read .blend (the status' plan / greybox / layout times), keeping the camera where it was.
// Without WebGL it shows the plan image (the top-down map) instead.

const C3D_COLORS = {tower: 0x7d86a6, midrise: 0xb39572, rowhouse: 0xa86b55, house: 0xdcc8a2, industry: 0x7f8f99,
  landmark: 0xf0e2a8, other: 0xbdbdbd};
const C3D_ROADS = {highway: 0x7b7b7b, boulevard: 0xb4afa2, avenue: 0xd2cdbf, street: 0xe2ded3};
const C3D_WATER = 0x3d6f98, C3D_PARK = 0x6d9a4f, C3D_PAVING = 0xc9c2b2, C3D_SKY = 0xc7d5e2;
let c3d = null;                 // the viewer, made on first use: {ok, T, renderer, scene, cams, groups, ...}
let c3dData = null, c3dKey = null, c3dLoading = false, c3dTop = false, c3dColorBy = "type";

function c3dShown() {
  return typeof catalogData !== "undefined" && catalogData && catalogData.city;
}

// Called with every Layout redraw (renderCatalog) and when the view first gets a size.
function city3dRefresh() {
  const box = $("#city3d");
  if (!box) return;
  box.hidden = !c3dShown();
  const view = $("#city3d-view");
  if (box.hidden || currentTab !== "city" || !view.clientWidth) return;
  const st = typeof statusData !== "undefined" && statusData ? statusData : {};
  const busy = activeJobs("site.plan").length + activeJobs("site.extract").length;
  const key = JSON.stringify([slug, st.plan_mtime, st.greybox_mtime, st.layout_mtime]);
  if (busy || c3dLoading || key === c3dKey) return;
  c3dKey = key;
  city3dLoad();
}

async function city3dLoad() {
  c3dLoading = true;
  c3dNote("Loading the city…");
  try {
    const d = await api(`/api/projects/${slug}/site/city3d`, undefined, "GET");
    if (!(await c3dInit())) { c3dFallback(); return; }
    const t0 = performance.now();
    c3dBuild(d);
    c3d.buildMs = performance.now() - t0;
    c3dRender();
  } catch (e) {
    c3dKey = null;
    c3dNote(`Couldn't load the 3D city: ${errorText(e)}`);
  } finally {
    c3dLoading = false;
  }
}

function c3dNote(text) { const n = $("#city3d-label"); if (n) n.textContent = text; }

function c3dFallback() {
  const view = $("#city3d-view");
  view.querySelectorAll("canvas").forEach(cv => cv.remove());
  let img = view.querySelector("img.c3d-plan");
  if (!img) { img = document.createElement("img"); img.className = "c3d-plan"; img.alt = "City plan (top-down)"; view.append(img); }
  if (catalogData && catalogData.plan_image) img.src = fileUrl(catalogData.plan_image, catalogData.plan_mtime);
  $("#city3d-top").disabled = true;
  c3dNote(`WebGL isn't available here (${(c3d && c3d.why) || "no WebGL"}): showing the top-down plan.`);
}

async function c3dInit() {
  if (c3d) return c3d.ok;
  const view = $("#city3d-view");
  try {
    const probe = document.createElement("canvas");
    if (!(probe.getContext("webgl2") || probe.getContext("webgl"))) throw new Error("no WebGL");
    const T = await import("/static/vendor/three/three.module.min.js");
    const {OrbitControls} = await import("/static/vendor/three/OrbitControls.js");
    const renderer = new T.WebGLRenderer({antialias: true});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    view.prepend(renderer.domElement);
    const scene = new T.Scene();
    scene.background = new T.Color(C3D_SKY);
    scene.add(new T.HemisphereLight(0xffffff, 0x8a7f6a, 1.6));
    const sun = new T.DirectionalLight(0xffffff, 1.9);
    sun.position.set(-0.5, -0.8, 1.2);
    scene.add(sun);
    const persp = new T.PerspectiveCamera(40, 1, 5, 60000);
    persp.up.set(0, 0, 1);
    const ortho = new T.OrthographicCamera(-1, 1, 1, -1, -20000, 20000);
    const orbit = new OrbitControls(persp, renderer.domElement);
    const pan = new OrbitControls(ortho, renderer.domElement);
    pan.enableRotate = false;
    pan.screenSpacePanning = true;
    pan.enabled = false;
    const groups = {};
    for (const g of ["terrain", "buildings", "roads", "nature", "water"]) { groups[g] = new T.Group(); scene.add(groups[g]); }
    c3d = {ok: true, T, renderer, scene, persp, ortho, orbit, pan, groups, meshes: [], frameMs: 0, buildMs: 0};
    orbit.addEventListener("change", c3dRender);
    pan.addEventListener("change", c3dRender);
    c3dWirePick();
    c3dResize();
    return true;
  } catch (e) {
    c3d = {ok: false, why: errorText(e)};
    return false;
  }
}

function c3dResize() {
  if (!c3d || !c3d.ok) return;
  const view = $("#city3d-view"), w = view.clientWidth, h = view.clientHeight;
  if (!w || !h) return;
  c3d.renderer.setSize(w, h);
  c3d.persp.aspect = w / h;
  c3d.persp.updateProjectionMatrix();
  const o = c3d.ortho, half = c3d.orthoHalf || 1000;
  o.left = -half * w / h; o.right = half * w / h; o.top = half; o.bottom = -half;
  o.updateProjectionMatrix();
  c3dRender();
}

let c3dFrame = 0;
function c3dRender() {
  if (!c3d || !c3d.ok || c3dFrame) return;
  c3dFrame = requestAnimationFrame(() => {
    c3dFrame = 0;
    const t0 = performance.now();
    c3d.renderer.render(c3d.scene, c3dTop ? c3d.ortho : c3d.persp);
    c3d.frameMs = performance.now() - t0;
    c3dLabel();
  });
}

function c3dLabel() {
  if (!c3dData) return;
  c3dNote(`${c3dData.buildings.length.toLocaleString()} buildings · built in ${Math.round(c3d.buildMs)} ms · `
    + `frame ${c3d.frameMs.toFixed(1)} ms`);
}

// --- the scene ---------------------------------------------------------------------------------

function c3dClear(group) {
  for (const o of [...group.children]) {
    group.remove(o);
    if (o.geometry) o.geometry.dispose();
    if (o.material) o.material.dispose();
  }
}

// The terrain height at (x, y): bilinear on the decimated grid (rows north -> south).
function c3dZ(d, x, y) {
  const n = d.terrain.n, e = d.extent, z = d.terrain.z;
  const fx = Math.min(Math.max((x / e + 0.5) * (n - 1), 0), n - 1.001), fy = Math.min(Math.max((0.5 - y / e) * (n - 1), 0), n - 1.001);
  const j = Math.floor(fx), i = Math.floor(fy), a = fx - j, b = fy - i;
  return z[i * n + j] * (1 - a) * (1 - b) + z[i * n + j + 1] * a * (1 - b) + z[(i + 1) * n + j] * (1 - a) * b + z[(i + 1) * n + j + 1] * a * b;
}

function c3dBuild(d) {
  const T = c3d.T, G = c3d.groups;
  const first = !c3dData || c3d.slug !== slug;
  c3dData = d;
  for (const g of Object.values(G)) c3dClear(g);
  // terrain, coloured by height; the sea a plane at sea level
  const n = d.terrain.n, geo = new T.PlaneGeometry(d.extent, d.extent, n - 1, n - 1);
  const pos = geo.attributes.position, col = new Float32Array(pos.count * 3);
  const sand = new T.Color(0xd9c9a3), grass = new T.Color(0xa7aa82), rock = new T.Color(0x9a958a), c = new T.Color();
  for (let i = 0; i < pos.count; i++) {
    const z = d.terrain.z[i];
    pos.setZ(i, z);
    const t = (z - d.sea_level) / 120;
    if (t < 0.02) c.copy(sand); else c.copy(grass).lerp(rock, Math.min(Math.max(t - 0.2, 0), 1));
    col.set([c.r, c.g, c.b], i * 3);
  }
  geo.setAttribute("color", new T.BufferAttribute(col, 3));
  geo.computeVertexNormals();
  G.terrain.add(new T.Mesh(geo, new T.MeshLambertMaterial({vertexColors: true})));
  if (d.terrain.z.some(z => z < d.sea_level)) {          // a coast: the sea (an inland terrain has none)
    const sea = new T.Mesh(new T.PlaneGeometry(d.extent * 3, d.extent * 3), new T.MeshLambertMaterial({color: C3D_WATER}));
    sea.position.z = d.sea_level;
    G.water.add(sea);
  }
  // ways: one merged ribbon mesh for the roads, one for the rivers and canals
  const roads = [], water = [];
  for (const w of d.ways) (w.kind === "river" || w.kind === "canal" ? water : roads).push(w);
  G.roads.add(c3dRibbons(roads, w => C3D_ROADS[w.kind] || C3D_ROADS.street, 2));
  G.water.add(c3dRibbons(water, () => C3D_WATER, 1));
  G.nature.add(c3dPolys(d, d.parks, C3D_PARK, 0.5));
  G.roads.add(c3dPolys(d, d.paving, C3D_PAVING, 0.45));
  G.water.add(c3dPolys(d, d.water, C3D_WATER, 0.3));
  c3dBuildings(d);
  c3dToggles();
  if (first) c3dFrameCity(d);
  c3d.slug = slug;
}

function c3dRibbons(ways, colorOf, offset) {
  const T = c3d.T, p = [], cl = [], c = new T.Color();
  for (const w of ways) {
    const pts = w.pts, h = w.w / 2;
    if (pts.length < 2) continue;
    c.set(colorOf(w));
    for (let i = 0; i < pts.length - 1; i++) {
      const [x0, y0, z0] = pts[i], [x1, y1, z1] = pts[i + 1];
      const L = Math.hypot(x1 - x0, y1 - y0) || 1, nx = -(y1 - y0) / L * h, ny = (x1 - x0) / L * h;
      p.push(x0 + nx, y0 + ny, z0, x0 - nx, y0 - ny, z0, x1 + nx, y1 + ny, z1,
             x1 + nx, y1 + ny, z1, x0 - nx, y0 - ny, z0, x1 - nx, y1 - ny, z1);
      for (let k = 0; k < 6; k++) cl.push(c.r, c.g, c.b);
    }
  }
  const geo = new T.BufferGeometry();
  geo.setAttribute("position", new T.Float32BufferAttribute(p, 3));
  geo.setAttribute("color", new T.Float32BufferAttribute(cl, 3));
  geo.setAttribute("normal", new T.Float32BufferAttribute(new Float32Array(p.length).map((_, i) => i % 3 === 2 ? 1 : 0), 3));
  const m = new T.MeshLambertMaterial({vertexColors: true, side: T.DoubleSide, polygonOffset: true,
    polygonOffsetFactor: -offset, polygonOffsetUnits: -offset * 2});
  return new T.Mesh(geo, m);
}

// Polygons (rings: exterior, then holes) laid on the terrain, merged into one mesh.
function c3dPolys(d, polys, color, lift) {
  const T = c3d.T, p = [], idx = [];
  for (const rings of polys) {
    if (!rings.length || rings[0].length < 3) continue;
    const shape = new T.Shape(rings[0].map(([x, y]) => new T.Vector2(x, y)));
    for (const h of rings.slice(1)) if (h.length >= 3) shape.holes.push(new T.Path(h.map(([x, y]) => new T.Vector2(x, y))));
    const g = new T.ShapeGeometry(shape), a = g.attributes.position, base = p.length / 3;
    for (let i = 0; i < a.count; i++) p.push(a.getX(i), a.getY(i), c3dZ(d, a.getX(i), a.getY(i)) + lift);
    const gi = g.index ? g.index.array : [...Array(a.count).keys()];
    for (const v of gi) idx.push(base + v);
    g.dispose();
  }
  const geo = new T.BufferGeometry();
  geo.setAttribute("position", new T.Float32BufferAttribute(p, 3));
  geo.setIndex(idx);
  geo.computeVertexNormals();
  return new T.Mesh(geo, new T.MeshLambertMaterial({color, side: T.DoubleSide, polygonOffset: true,
    polygonOffsetFactor: -1, polygonOffsetUnits: -2}));
}

// One instanced box mesh per building type; each instance knows its row (for picking).
function c3dBuildings(d) {
  const T = c3d.T, G = c3d.groups, box = new T.BoxGeometry(1, 1, 1);
  box.translate(0, 0, 0.5);
  const byType = d.types.map(() => []);
  d.buildings.forEach((b, i) => byType[b[7]].push(i));
  const m = new T.Matrix4(), q = new T.Quaternion(), zAxis = new T.Vector3(0, 0, 1), v = new T.Vector3(), s = new T.Vector3();
  c3d.meshes = [];
  byType.forEach((rows, ti) => {
    if (!rows.length) return;
    const mesh = new T.InstancedMesh(box, new T.MeshLambertMaterial({color: 0xffffff}), rows.length);
    rows.forEach((ri, k) => {
      const b = d.buildings[ri];
      q.setFromAxisAngle(zAxis, b[6] * Math.PI / 180);
      m.compose(v.set(b[0], b[1], b[2]), q, s.set(Math.max(b[3], 0.5), Math.max(b[4], 0.5), Math.max(b[5], 0.5)));
      mesh.setMatrixAt(k, m);
    });
    mesh.userData = {rows, type: d.types[ti]};
    mesh.frustumCulled = false;
    mesh.computeBoundingSphere();
    G.buildings.add(mesh);
    c3d.meshes.push(mesh);
  });
  c3dColor();
}

// Colour by type, or by height (low warm, tall deep blue).
function c3dColor() {
  if (!c3d || !c3d.meshes) return;
  const T = c3d.T, c = new T.Color(), lo = new T.Color(0xe8d9b0), mid = new T.Color(0xc4724e), hi = new T.Color(0x3b4a8c);
  for (const mesh of c3d.meshes) {
    const {rows, type} = mesh.userData;
    rows.forEach((ri, k) => {
      if (c3dColorBy === "height") {
        const t = Math.min(Math.max(Math.log(c3dData.buildings[ri][5] / 4) / Math.log(60), 0), 1);
        if (t < 0.5) c.copy(lo).lerp(mid, t * 2); else c.copy(mid).lerp(hi, (t - 0.5) * 2);
      } else c.set(C3D_COLORS[type] || C3D_COLORS.other);
      mesh.setColorAt(k, c);
    });
    mesh.instanceColor.needsUpdate = true;
  }
  c3dLegend();
  c3dRender();
}

function c3dLegend() {
  const el = $("#city3d-legend");
  if (!el || !c3dData) return;
  el.innerHTML = c3dColorBy === "height"
    ? `<span class="c3d-ramp"></span> low → tall`
    : c3dData.types.filter(t => c3d.meshes.some(m => m.userData.type === t)).map(t =>
        `<span class="c3d-key"><i style="background:#${(C3D_COLORS[t] || C3D_COLORS.other).toString(16).padStart(6, "0")}"></i>${esc(t)}</span>`).join("");
}

// Frame the city: an oblique view from the south over the bounds; the plan view fits them.
function c3dFrameCity(d) {
  const [x0, y0, x1, y1] = d.bounds, cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, span = Math.max(x1 - x0, y1 - y0);
  const z = c3dZ(d, cx, cy);
  c3d.orbit.target.set(cx, cy, z);
  c3d.persp.position.set(cx, cy - 0.75 * span, z + 0.6 * span);
  c3d.orbit.update();
  c3d.orthoHalf = 0.55 * Math.max(y1 - y0, (x1 - x0) / Math.max(c3d.persp.aspect, 0.1));
  c3d.ortho.position.set(cx, cy, z + 5000);
  c3d.ortho.lookAt(cx, cy, z);
  c3d.pan.target.set(cx, cy, z);
  c3d.ortho.zoom = 1;
  c3dResize();
  c3d.pan.update();
}

function c3dToggles() {
  if (!c3d || !c3d.ok) return;
  for (const g of ["buildings", "roads", "nature", "water"]) {
    const box = document.querySelector(`#city3d [data-c3d-show="${g}"]`);
    c3d.groups[g].visible = !box || box.checked;
  }
  c3dRender();
}

function c3dSetTop(on) {
  c3dTop = on;
  const b = $("#city3d-top");
  b.textContent = on ? "3D view" : "Top-down";
  b.setAttribute("aria-pressed", on);
  if (c3d && c3d.ok) { c3d.orbit.enabled = !on; c3d.pan.enabled = on; c3dRender(); }
}

// --- hover and click: what a building is -------------------------------------------------------

function c3dWirePick() {
  const T = c3d.T, ray = new T.Raycaster(), ndc = new T.Vector2(), el = c3d.renderer.domElement, tip = $("#city3d-tip");
  let pending = null;
  const pick = ev => {
    const r = el.getBoundingClientRect();
    ndc.set((ev.clientX - r.left) / r.width * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, c3dTop ? c3d.ortho : c3d.persp);
    const hit = c3d.groups.buildings.visible && ray.intersectObjects(c3d.meshes, false)[0];
    if (!hit) return null;
    const b = c3dData.buildings[hit.object.userData.rows[hit.instanceId]];
    const storeys = Math.max(1, Math.round(b[5] / (c3dData.storey_m || 3.4)));
    return `${hit.object.userData.type} · ${b[8] >= 0 ? esc(c3dData.districts[b[8]]) : "no district"} · `
      + `${b[5].toFixed(0)} m (${storeys} storey${storeys === 1 ? "" : "s"}) · ${b[3].toFixed(0)} × ${b[4].toFixed(0)} m`;
  };
  el.addEventListener("pointermove", ev => {
    if (pending) return;
    pending = requestAnimationFrame(() => {
      pending = null;
      const text = pick(ev);
      tip.hidden = !text;
      if (text) {
        const r = $("#city3d-view").getBoundingClientRect();
        tip.innerHTML = text;
        tip.style.left = `${ev.clientX - r.left + 14}px`;
        tip.style.top = `${ev.clientY - r.top + 10}px`;
      }
    });
  });
  el.addEventListener("pointerleave", () => { tip.hidden = true; });
  el.addEventListener("click", ev => {
    const text = pick(ev);
    $("#city3d-info").innerHTML = text ? `Selected: ${text}` : "";
  });
}

// a size change (the window, or the Layout tab shown) resizes the view and draws it when first seen
new ResizeObserver(() => { c3dResize(); guarded(city3dRefresh); }).observe($("#city3d-view"));
$("#city3d-top").onclick = () => c3dSetTop(!c3dTop);
$("#city3d-color").onchange = e => { c3dColorBy = e.target.value; c3dColor(); };
document.querySelectorAll("#city3d [data-c3d-show]").forEach(b => { b.onchange = c3dToggles; });
$("#city3d-reset").onclick = () => { if (c3d && c3d.ok && c3dData) { c3dFrameCity(c3dData); c3dRender(); } };
