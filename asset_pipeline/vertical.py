"""Height fit and vertical roles of procedural builds in their slots, for any project: kit modules
(kit_assembly) and single-building slots (city_library).

A build placed in a slot taller (or lower) than itself is rebuilt at the slot's height: a height
variant with the storey count that fills the slot (within one storey), cached per role and storey
count in 3d/library/<asset>/heights/<role>-<storeys>s-<parts hash>.glb (index.json: the brief and
parts each was built from; a builder that stretches with its height, not by storeys, gets one per
height).

Only when that isn't possible (beyond the builder's MAX_STOREYS, a builder whose height is fixed,
a model with no building brief such as a TRELLIS mesh) are layers stacked, each with a vertical
role:
    single  the whole building (ground floor and roof features)
    bottom  ground floor on, roof features off
    middle  no ground floor, no roof features: plain storeys
    top     no ground floor, roof features on
A tier of a slot's masses (a setback standing on a lower mass, kit_assembly.tiers) has a role too:
its layers take bottom / middle / top within it (`compose`), so stacked masses read as one
building. Models with no brief stack as copies (no roles: the placement check flags them).

Roles map onto each builder's own settings (`role_features`): ground kinds, the perimeter's top
storeys, the arcade's attic, the tower's crown, parapets, cornices, gardens. Where a builder had
no way to turn a part off, it reads `ground_floor` / `roof_features` (row: per-house types;
podium_tower: the podium; arcade: the arcade storey) and facades take the ground kind "upper"
(building_parts.FACADE). Builders without role support (ROLE_FORMS) fit their height or stack
as copies.
"""
import copy
import hashlib
import json
import math
import time

from .project import ProjectStore, read_json, write_json

HV_LOG = ": height variant "     # the log line each variant build starts with (ue_build counts them)
ROLES = ("single", "bottom", "middle", "top")
FLAGS = {"single": (True, True), "bottom": (True, False), "middle": (False, False), "top": (False, True)}   # (ground, roof)
# the most storeys a builder makes in one build (beyond that: layers)
MAX_STOREYS = {"arcade": 16, "perimeter": 12, "row": 6, "podium_tower": 60}
DEFAULT_MAX = 12
ROLE_FORMS = ("arcade", "perimeter", "row", "podium_tower")
DIR = "heights"


def storey_h(form: str, f: dict, p: dict) -> float:
    """The storey height a form's height variants step by."""
    if form == "arcade":
        from .building_gen import arcade_spec
        A = arcade_spec(f.get("arcade")) or {}
        return float((A.get("gallery") or {}).get("storey_h") or A.get("h") or 3.4)
    return float(p.get("storey_h") or f.get("storey_h") or 3.4)


def role_features(form: str, f: dict, p: dict, role: str) -> dict:
    """The features a layer of this role is built with (a copy; single: unchanged)."""
    if role not in FLAGS:
        raise ValueError(f"vertical role must be one of {ROLES}")
    f = copy.deepcopy(f)
    if role == "single":
        return f
    ground, roof = FLAGS[role]
    f["ground_floor"], f["roof_features"] = ground, roof
    if not ground:
        f["annexes"] = []                                # low volumes against the ground storey
    if form == "perimeter":
        f["accent"] = None                               # a volume rising through every layer: single builds only
        specs = [f[k] for k in ("street", "street_side", "court") if f.get(k)]
        if not ground:
            f["ground_h"] = storey_h(form, f, p)
            f["court_garden"] = None
            f["street_garden"] = None
            for F in specs:
                F["ground"] = {"kind": "upper"}
        if not roof:
            f["top"] = None                              # the set-back top storeys and their terrace
            for F in specs:
                F.update(parapet=0.0, cornice=None)
    elif form == "row":
        if role != "top":
            f["steps"] = [0]                             # a layer under another: one roof line
        if not roof:
            f["roof_box"] = None
            f["roof"] = {"pergola": False, "planters": False, "box": False}
    elif form == "podium_tower":
        if not roof:
            f["crown"] = None
            f["corner_rise"] = 0.0
    elif form == "arcade":
        if not roof:
            f["attic"] = None
            f["hip_roof"] = None
            f["arcade"] = {**(f.get("arcade") or {}), "parapet": 0.0, "cornice": None}
    return f


def compose(tier: str, k: int) -> list[str]:
    """The roles of k layers, bottom first, in a tier of this role."""
    if k <= 1:
        return [tier]
    head = {"single": "bottom", "bottom": "bottom"}.get(tier, "middle")
    tail = {"single": "top", "top": "top"}.get(tier, "middle")
    return [head] + ["middle"] * (k - 2) + [tail]


def _top(parts) -> float:
    from .building_parts import top_of
    return max((t for t in map(top_of, parts) if math.isfinite(t)), default=0.0)


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


class Fitter:
    """Height variants of procedural builds for one project. build(store, asset, out, dims,
    role, maps): builds one GLB (default: building_proc.build in Blender); tests pass their own.
    dry: build nothing, only list the variants a build would make (`missing`), with their bounds
    from the parts (`bounds`, which city_library.plan reads instead of the missing GLBs)."""

    def __init__(self, store: ProjectStore, build=None, log=print, dry: bool = False):
        self.store, self.log, self.dry = store, log, dry
        self._build = build or _blender_build
        self._specs: dict = {}
        self._plans: dict = {}
        self.built: list[str] = []
        self.build_s = 0.0                  # seconds spent building them (estimates: per variant)
        self.missing: list[str] = []        # dry: the variants' GLBs (relative to the project) a build would make
        self.bounds: dict = {}              # {glb: [lo, hi]}, shared with the plan

    def spec(self, asset: str) -> dict | None:
        """{form, f, p, dims} of an asset's building brief, or None (no brief: a TRELLIS model)."""
        if asset not in self._specs:
            from . import kit_proc
            from .building_gen import FORMS, features
            b = read_json(kit_proc.spec_path(self.store, asset), default=None)
            if not b or b.get("kind") != "building" or b.get("form") not in FORMS:
                self._specs[asset] = None
            else:
                meas = b.get("measured") or {}
                p = {k: meas[k] for k in ("bay_m", "storey_h") if meas.get(k)} | (b.get("params") or {})
                self._specs[asset] = {"form": b["form"], "f": features(b["form"], b.get("features")), "p": p,
                                      "dims": [float(x) for x in b["dims"]]}
        return self._specs[asset]

    def parts(self, asset: str, role: str, h: float) -> list[dict]:
        from .building_proc import generate
        s = self.spec(asset)
        return generate(s["form"], [*s["dims"][:2], h], s["p"], role_features(s["form"], s["f"], s["p"], role))

    def layers(self, asset: str, H: float, tier: str = "single") -> list[dict] | None:
        """How asset fills H m in a tier of that role: [{role, h (the height it is built at),
        walls (its roof's height), top (m), storeys, sha}], bottom first; one layer when it fits.
        None: no brief (stack copies). Of one build (height fit), layers splitting the storeys
        (beyond MAX_STOREYS) and copies of its own height (a builder that can't reach H): the one
        with the fewest layers that comes within a storey of H (between its roof and its top),
        else the closest."""
        s = self.spec(asset)
        if s is None:
            return None
        key = (asset, round(H, 1), tier)
        if key in self._plans:
            return self._plans[key]
        form = s["form"]
        st = storey_h(form, s["f"], s["p"])
        N = max(1, round(H / st))
        k0 = max(1, math.ceil(N / MAX_STOREYS.get(form, DEFAULT_MAX)))
        plans = []
        for k in ((k0, k0 + 1) if form in ROLE_FORMS else (1,)):
            if k > N:
                continue
            n = [N // k + (i < N % k) for i in range(k)]            # storeys per layer, the bigger ones low
            plan = [self._layer(asset, r, H * ni / N, st) for r, ni in zip(compose(tier, k), n)]
            miss = H - sum(x["walls"] for x in plan)                  # the storeys' rounding: the top layer takes it
            if k > 1 and abs(miss) > st / 2 and plan[-1]["h"] + miss > st:
                plan[-1] = self._layer(asset, plan[-1]["role"], plan[-1]["h"] + miss, st)
            plans.append(plan)
        own = self._layer(asset, "single", s["dims"][2], st)["walls"] or s["dims"][2]
        kc = max(1, round(H / own))
        if kc > 1:
            roles = compose(tier, kc) if form in ROLE_FORMS else ["single"] * kc
            plans.append([self._layer(asset, r, s["dims"][2], st) for r in roles])

        def err(plan):                                   # how far H is outside its roof .. its top (a crown, an attic)
            walls = sum(x["walls"] for x in plan)
            return max(0.0, walls - H, H - (walls - plan[-1]["walls"] + plan[-1]["top"]))
        plans.sort(key=len)                              # the fewest layers within a storey, else the closest
        self._plans[key] = next((pl for pl in plans if err(pl) <= st), None) or min(plans, key=err)
        return self._plans[key]

    def _layer(self, asset, role, h, st) -> dict:
        from .building_parts import top_of
        parts = self.parts(asset, role, h)
        top = _top(parts)
        walls = max((top_of(p) for p in parts if p.get("mat") == "roof"), default=top)
        return {"role": role, "h": round(h, 3), "walls": round(walls, 3), "top": round(top, 3),
                "storeys": max(1, round(walls / st)), "sha": _sha(parts)}

    def glb(self, asset: str, layer: dict) -> str:
        """The GLB (relative to the project) of one layer: the asset's own build when the layer
        is it, else its height variant, built when missing or made from other parts."""
        from . import kit_proc
        from .areas import brief_sha
        s = self.spec(asset)
        lib = kit_proc.out_dir(self.store, asset)
        own = lib / "procedural.glb"
        if layer["role"] == "single" and own.is_file() and self._layer(asset, "single", s["dims"][2], 1.0)["sha"] == layer["sha"]:
            return own.relative_to(self.store.root).as_posix()
        d = lib / DIR
        name = f"{layer['role']}-{layer['storeys']}s-{layer['sha'][:6]}"      # (builders that stretch: one per height)
        out = d / f"{name}.glb"
        idx = read_json(d / "index.json", default={}) or {}
        sha = f"{brief_sha(self.store, asset)}:{layer['sha']}"
        rel = out.relative_to(self.store.root).as_posix()
        if not (out.is_file() and (idx.get(name) or {}).get("sha") == sha):
            if self.dry:
                if rel not in self.missing:
                    w, dd = s["dims"][:2]
                    self.missing.append(rel)
                    self.bounds[rel] = [[-w / 2, -dd / 2, 0.0], [w / 2, dd / 2, layer["top"]]]
                return rel
            d.mkdir(parents=True, exist_ok=True)
            self.log(f"{asset}{HV_LOG}{name} ({layer['h']:.1f} m)")
            t = time.time()
            self._build(self.store, asset, out, [*s["dims"][:2], layer["h"]], layer["role"], d / "maps")
            self.build_s += time.time() - t
            idx[name] = {"sha": sha, "role": layer["role"], "storeys": layer["storeys"], "h": layer["h"], "walls": layer["walls"],
                         "top": layer["top"]}
            write_json(d / "index.json", idx)
            self.built.append(f"{asset}/{name}")
        return rel


def _blender_build(store, asset, out, dims, role, maps):
    from . import building_proc
    building_proc.build(store, asset, log=lambda *a: None, out=out, dims=dims, maps=maps, role=role)


def stack_lifts(bounds: list) -> list[float]:
    """Each layer's lift over the first's origin: each stands with its base (lo z) on the top
    (hi z) of the one under it. bounds: [(lo, hi)] bottom first."""
    out = [0.0]
    for (lo0, hi0), (lo1, _) in zip(bounds, bounds[1:]):
        out.append(out[-1] + hi0[2] - lo1[2])
    return out
