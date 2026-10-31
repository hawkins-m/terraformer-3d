"""Merge ground surfaces (project.json unreal.merge_ground; the Unreal tab, `ap ue build --merge-ground`):
the export joins the street, road and field surfaces into tiles of about TILE_M, one mesh per tile,
slot type and piece, instead of one mesh and one actor per slot. A metro city has ~1,000 such slots,
each a unique mesh (draped ribbons and fields), and importing them was most of its Unreal build.

    off    one actor per street, road and field slot (as before)
    auto   merge for a metro city only (city_size), the default
    on     always merge

Only draped pieces merge: a slot's own mesh "<slot>.<piece>" (blender_greybox merges those per slot
and piece). Shared meshes (kit pieces, buildings, trees, stair treads), vegetation, rocks and water
never do. A merged tile keeps its pieces' slot type (so its tint and paving dressing stay) and stands
at its tile's centre, its mesh in world space relative to it: the same look and placement. Merged
ground can't be moved piece by piece in Unreal.
"""
import math

from .project import ProjectStore

TILE_M = 1000.0
# the slot types whose draped surfaces merge: streets, roads, paving where roads meet, fields. A tile
# holds one surface: all its streets, all its fields (crop and fallow look alike: the tint is the slot
# type's), its roads per road type (each type keeps its own tint and paving)
TYPES = ("street", "avenue", "boulevard", "highway", "lane", "interchange", "culdesac", "farmland")
SURFACE = {"farmland": "field"}
MODES = ("off", "auto", "on")


def mode(store: ProjectStore) -> str:
    from .dressing import _unreal
    return _unreal(store).merge_ground


def wanted(store: ProjectStore, layout=None) -> bool:
    """Does this export merge? on; auto for a metro city."""
    m = mode(store)
    if m != "auto":
        return m == "on"
    if layout is None:
        from .stages import sw_site
        try:
            layout = sw_site.load_layout(store)
        except FileNotFoundError:
            return False
    return layout.city is not None and layout.city.size == "metro"


def save_mode(store: ProjectStore, value: str) -> None:
    if value not in MODES:
        raise ValueError(f"merge_ground must be one of {', '.join(MODES)}")
    from .dressing import _unreal
    p = store.load()
    u = _unreal(store).model_copy(deep=True)
    u.merge_ground = value
    p.unreal = u
    store.save(p)


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def plan(gb: dict) -> dict:
    """{tile name: {type, piece (its surface), origin [x, y, 0], parts [[mesh, Blender world 4x4]]}} and
    the pieces they take ({slot id: {piece names}}), from greybox.json."""
    tiles: dict[str, dict] = {}
    taken: dict[str, set] = {}
    for s in gb["slots"]:
        if s["type"] not in TYPES:
            continue
        for p in s["pieces"]:
            if p["mesh"] != f"{s['id']}.{p['piece']}":       # a shared mesh: stays as it is
                continue
            W = _mul(s["matrix"], p["matrix_local"])
            i, j = math.floor(W[0][3] / TILE_M), math.floor(W[1][3] / TILE_M)
            surface = SURFACE.get(s["type"], s["type"])
            name = f"ground.{surface}.x{i}y{j}"
            t = tiles.setdefault(name, {"type": s["type"], "piece": surface, "parts": [],
                                        "origin": [(i + 0.5) * TILE_M, (j + 0.5) * TILE_M, 0.0]})
            t["parts"].append([p["mesh"], W])
            taken.setdefault(s["id"], set()).add(p["name"])
    return {"tiles": tiles, "taken": {k: sorted(v) for k, v in taken.items()}}


def apply(m: dict, gb: dict, index: dict, merge: dict) -> dict:
    """The manifest with the merged pieces out of their slots (a slot left with none is dropped) and
    one slot per tile, its mesh at the tile's centre. Returns {tiles, slots_merged, pieces}."""
    from . import ue_coords
    from .stages.s7_export import _tint, label, manifest_components
    taken = {k: set(v) for k, v in merge["taken"].items()}
    by_id = {s["id"]: s for s in gb["slots"]}
    keep = []
    for s in m["slots"]:
        if s["id"] not in taken:
            keep.append(s)
            continue
        rest = [p for p in by_id[s["id"]]["pieces"] if p["name"] not in taken[s["id"]]]
        if rest:
            keep.append(s | {"components": manifest_components(rest, index)})
    have = {a["id"] for a in m["assets"]}
    for name, t in sorted(merge["tiles"].items()):
        e = index[name]
        if e["id"] not in have:
            m["assets"].append({"id": e["id"], "mesh": name, "glb": f"assets/{e['glb']}", "faces": e["faces"],
                                "bounds_m": e["bounds"], "tint": _tint(t["type"], t["piece"]), "textures": {}})
            have.add(e["id"])
        sid = name.replace(".", "-")
        o = t["origin"]
        M = [[1.0, 0.0, 0.0, o[0]], [0.0, 1.0, 0.0, o[1]], [0.0, 0.0, 1.0, o[2]], [0.0, 0.0, 0.0, 1.0]]
        keep.append({"id": sid, "type": t["type"], "category": "terrain", "kit": None, "district": None, "material": None,
                     "label": label("ground", sid), "folder": "AP/ground/merged",
                     "tags": [f"ap:id={sid}", f"ap:type={t['type']}", "ap:source=pipeline", "ap:merged=ground"],
                     "transform": ue_coords.to_ue(M),
                     "components": [{"asset": e["id"], "instances": [ue_coords.to_ue([[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]])]}]})
    m["slots"] = keep
    m["assets"].sort(key=lambda a: a["id"])
    out = {"tiles": len(merge["tiles"]), "slots_merged": len(taken),
           "pieces": sum(len(v) for v in taken.values())}
    m["ground_merged"] = out
    return out
