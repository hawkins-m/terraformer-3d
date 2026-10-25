"""Variant sets: several library entries for one prop type, so a market's canopy tables are not
all the same table (Library › Asset review › Manage entry… › Variant set; `ap library variants`).

A set is keyed by its base entry (the prop type's own entry, e.g. canopy-table) and kept in the
library decisions file (sw_library.decisions_path) under "_variants":
    {"canopy-table": {"members": {"canopy-table": 1.0, "stall-b": 2.0, "stall-c": 1.0},
                      "areas": [], "seed": 0}}
members: entry -> weight (the base is always a member; its weight may be 0 to leave it out);
areas: the areas whose prop slots pick from the set (empty: every slot of the prop type).

A prop slot is one place the greybox holds that prop: a canopy table in a market, keyed by its
holding slot and its position there (`market-0@-51.4,3.6`). Each picks one member by weighted
rendezvous hashing of (seed, slot key, member): the same slot always gets the same entry, the
shares follow the weights, and adding a member (or raising its weight) only moves slots to that
member; the others keep theirs. Members of a set join the base's areas (areas.members), so the
area loop makes their sheets and 3D too.
"""
import hashlib
import math

import numpy as np

from .project import ProjectStore, write_json
from .stages import sw_library

KEY = "_variants"
# the piece that stands for each prop instance (one per prop, as sw_library._props_of counts)
MAIN_PIECES = {"market:hut": "market hut", "market:tent": "market tent", "market:canopy": "canopy table",
               "market:stall": "market stall"}


def _entries(store: ProjectStore) -> dict:
    data = sw_library.load(store) or {}
    return {e["id"]: e for e in data.get("entries", []) + data.get("unseen", [])}


def sets(store: ProjectStore) -> dict:
    return sw_library.decisions(store).get(KEY) or {}


def _save(store: ProjectStore, s: dict) -> None:
    d = sw_library.decisions(store)
    if s:
        d[KEY] = s
    else:
        d.pop(KEY, None)
    write_json(sw_library.decisions_path(store), d)


def set_of(store: ProjectStore, entry: str) -> str | None:
    """The base of the set an entry belongs to (as base or member), or None."""
    for base, spec in sets(store).items():
        if entry == base or entry in (spec.get("members") or {}):
            return base
    return None


def noun(store: ProjectStore, entry: str) -> str | None:
    """The prop type (greybox noun) an entry stands for: a plan prop's part, or None."""
    e = _entries(store).get(entry) or {}
    return e.get("part") if e.get("kind") == "prop" else None


def save(store: ProjectStore, base: str, members: dict, areas: list[str] | None = None, seed: int | None = None) -> dict:
    """Create or replace the set of `base`: members {entry: weight >= 0} (the base is kept as a
    member; leave an entry out to remove it), areas (empty: everywhere)."""
    ents = _entries(store)
    if base not in ents:
        raise KeyError(f"no library entry {base}")
    if ents[base].get("kind") != "prop" or ents[base].get("custom"):
        raise ValueError(f"{base}: a variant set is based on a prop type of the library plan (e.g. canopy-table)")
    from . import areas as areas_
    known = set(areas_.load(store))
    bad = [a for a in areas or [] if a not in known]
    if bad:
        raise ValueError(f"no area(s) {', '.join(bad)}")
    clean = {}
    for m, w in {base: (members or {}).get(base, 1.0), **(members or {})}.items():
        if m not in ents:
            raise KeyError(f"no library entry {m}")
        if ents[m].get("kind") != "prop":
            raise ValueError(f"{m} is a {ents[m].get('kind')}, not a prop")
        other = set_of(store, m)
        if other not in (None, base):
            raise ValueError(f"{m} is already in the variant set of {other}")
        if isinstance(w, bool) or not isinstance(w, (int, float)) or not math.isfinite(w) or w < 0:
            raise ValueError(f"weight of {m}: a number 0 or more")
        clean[m] = float(w)
    if not any(w > 0 for w in clean.values()):
        raise ValueError("at least one member needs a weight above 0")
    s = sets(store)
    old = s.get(base) or {}
    s[base] = {"members": clean, "areas": list(areas if areas is not None else old.get("areas") or []),
               "seed": int(seed if seed is not None else old.get("seed", 0))}
    _save(store, s)
    return summary(store, base)


def remove(store: ProjectStore, base: str) -> None:
    s = sets(store)
    if base not in s:
        raise KeyError(f"{base} has no variant set")
    s.pop(base)
    _save(store, s)


def prop_slots(store: ProjectStore, prop: str) -> list[dict]:
    """Every place the greybox holds one `prop` (a noun): [{key, slot, x, y, z, yaw}] in world metres."""
    from .stages import sw_site
    out = []
    for s in sw_site.load_greybox(store)["slots"]:
        M = np.asarray(s["matrix"], float)
        for p in s.get("pieces") or []:
            if MAIN_PIECES.get(p["piece"]) != prop:
                continue
            L = np.asarray(p["matrix_local"], float)
            W = M @ L
            out.append({"key": f"{s['id']}@{L[0, 3]:.1f},{L[1, 3]:.1f}", "slot": s["id"],
                        "x": round(float(W[0, 3]), 3), "y": round(float(W[1, 3]), 3), "z": round(float(W[2, 3]), 3),
                        "yaw": round(math.degrees(math.atan2(W[1, 0], W[0, 0])), 2)})
    return sorted(out, key=lambda r: r["key"])


def pick(seed: int, key: str, members: dict) -> str:
    """The member a prop slot gets: weighted rendezvous hashing (highest -w / ln u wins)."""
    best, score = None, -math.inf
    for m in sorted(members):
        w = members[m]
        if w <= 0:
            continue
        h = hashlib.sha256(f"{seed}|{key}|{m}".encode()).digest()
        u = (int.from_bytes(h[:8], "big") + 0.5) / 2 ** 64
        sc = -w / math.log(u)
        if sc > score:
            best, score = m, sc
    return best


def assign(store: ProjectStore, base: str, area: str | None = None) -> list[dict]:
    """The prop slots of the set's prop type, each with the entry it picks: [{key, slot, x, y, z,
    yaw, entry}]. Slots outside the set's areas keep the base entry; area: only that area's slots."""
    from . import areas as areas_
    spec = sets(store).get(base)
    if spec is None:
        raise KeyError(f"{base} has no variant set")
    prop = noun(store, base)
    if not prop:
        raise ValueError(f"{base} is not a prop type")
    scope = None
    if spec.get("areas"):
        scope = set().union(*(areas_.slots_in(store, a) for a in spec["areas"]))
    only = areas_.slots_in(store, area) if area else None
    out = []
    for r in prop_slots(store, prop):
        if only is not None and r["slot"] not in only:
            continue
        inside = scope is None or r["slot"] in scope
        out.append(r | {"entry": pick(spec.get("seed", 0), r["key"], spec["members"]) if inside else base})
    return out


def summary(store: ProjectStore, base: str) -> dict:
    """The set with its members' weights, their share of the weights and how many slots each gets."""
    spec = sets(store)[base]
    rows = assign(store, base)
    total = sum(spec["members"].values()) or 1.0
    counts = {m: 0 for m in spec["members"]}
    for r in rows:
        counts[r["entry"]] = counts.get(r["entry"], 0) + 1
    return {"base": base, "prop": noun(store, base), "areas": spec.get("areas") or [], "seed": spec.get("seed", 0),
            "slots": len(rows), "members": [{"entry": m, "weight": w, "share": round(w / total, 3), "slots": counts.get(m, 0)}
                                            for m, w in spec["members"].items()]}


# --- duplicates as variants ------------------------------------------------------------------
# A duplicate (Manage entry… › Duplicate) is a variant of its original unless the user turns it
# off ("variant": false on its custom entry; Manage entry… › Use as a variant of <original>).
# Variants share the original's placements: the city's props of its type and the slots its kit
# build stands in (city_library). Each placement gets the original or one of its variants with a
# built Active version: placements are ordered by a hash of (original, placement key) and dealt
# out in turn, so the split is even (within one), and the same between builds while the
# placements and the variants with a build stay the same. Typed buildings (one instance build per
# planned building) always keep their own builds.

def duplicates_as_variants(store: ProjectStore) -> dict[str, str]:
    """{variant: its original} for every duplicate used as a variant (a duplicate of a variant
    counts for the first original)."""
    from .stages import sw_library as swl
    cust = swl.custom(store)
    ents = _entries(store)

    def root(eid, seen=()):
        e = cust.get(eid) or {}
        src = e.get("duplicate_of")
        if not src or src not in ents or eid in seen or e.get("variant") is False:
            return eid
        return root(src, (*seen, eid))
    out = {}
    for eid, e in cust.items():
        if e.get("duplicate_of") and e.get("variant", True) is not False and eid in ents:
            r = root(eid)
            if r != eid:
                out[eid] = r
    return out


def variant_of(store: ProjectStore, eid: str) -> str | None:
    return duplicates_as_variants(store).get(eid)


def set_variant(store: ProjectStore, eid: str, on: bool) -> dict:
    """Manage entry… › Use as a variant of <original> (duplicates only)."""
    from .stages import sw_library as swl
    cust = swl.custom(store)
    if not (cust.get(eid) or {}).get("duplicate_of"):
        raise ValueError(f"{eid} is not a duplicate: only a duplicate can be a variant of its original")
    cust[eid]["variant"] = bool(on)
    swl.save_custom(store, cust)
    return variant_info(store, eid)


def has_build(store: ProjectStore, eid: str) -> bool:
    """A built Active version (what a variant needs before it takes placements)."""
    from . import versions
    try:
        return versions.current_glb(store, eid) is not None
    except (FileNotFoundError, KeyError, ValueError):
        return False


def placement_keys(store: ProjectStore, original: str, gb: dict | None = None) -> list[str]:
    """The keys of the placements an original's variants share, as city_library keys them: its
    props in every slot (the prop's main pieces) and the slots its kit build stands in."""
    from .city_library import PROP_MAINS, prop_groups
    from .stages import sw_site
    from .stages.sw_library import PROP_PIECES, _slug
    if gb is None:
        try:
            gb = sw_site.load_greybox(store)
        except FileNotFoundError:
            return []
    from . import kit_proc
    from .project import read_json
    e = _entries(store).get(original) or {}
    has_instances = (store.root / "3d" / "library" / original / "instances" / "index.json").is_file()
    sp = kit_proc.spec_path(store, original)
    brief = (read_json(sp, default=None) or {}) if sp.is_file() else None
    slots = {s["id"]: s for s in gb["slots"]}
    keys = set()
    for s in gb["slots"]:
        for main, n, _ in prop_groups(s, PROP_PIECES, PROP_MAINS):
            if _slug(n) == original:
                keys.add(f"{original}_{s['id']}_{main}".replace("-", "m"))
    for sid in e.get("slot_ids") or [] if brief is not None else []:   # kit slots (look._candidates) and
        s = slots.get(sid)                           # typed slots no instance build covers (representative)
        if s is not None and (s.get("typology") and not has_instances or not s.get("typology") and brief.get("kind") == "building"):
            keys.add(f"{original}_{sid}".replace("-", "m"))
    return sorted(keys)


def deal(original: str, keys: list[str], members: list[str]) -> dict[str, str]:
    """{placement key: member}: keys in hash order, members dealt out in turn (original first)."""
    if not members:
        return {}
    order = sorted(keys, key=lambda k: (hashlib.sha256(f"{original}|{k}".encode()).hexdigest(), k))
    return {k: members[i % len(members)] for i, k in enumerate(order)}


def pools(store: ProjectStore) -> dict[str, dict]:
    """{original: {"variants": [all its variants], "built": [those with a built Active version]}}."""
    out: dict[str, dict] = {}
    for v, o in sorted(duplicates_as_variants(store).items()):
        p = out.setdefault(o, {"variants": [], "built": []})
        p["variants"].append(v)
        if has_build(store, v):
            p["built"].append(v)
    return out


def assignments(store: ProjectStore, gb: dict | None = None) -> dict[str, dict[str, str]]:
    """{original: {placement key: entry}} for every original with a built variant."""
    out = {}
    for o, p in pools(store).items():
        if p["built"]:
            out[o] = deal(o, placement_keys(store, o, gb), [o, *p["built"]])
    return out


def splits(store: ProjectStore) -> dict[str, dict]:
    """{original: {placements, counts {entry: placements} (original first), variants, waiting
    (variants with no built Active version yet)}}: what Library › Asset review and Views and 3D
    show on the original's row."""
    out = {}
    for o, p in pools(store).items():
        keys = placement_keys(store, o)
        a = deal(o, keys, [o, *p["built"]])
        counts = {m: 0 for m in [o, *p["built"]]}
        for m in a.values():
            counts[m] += 1
        out[o] = {"placements": len(keys), "counts": counts, "variants": p["variants"],
                  "waiting": [v for v in p["variants"] if v not in p["built"]]}
    return out


def variant_info(store: ProjectStore, eid: str) -> dict:
    """Manage entry…: {duplicate_of, variant (on/off), original (when on), built}."""
    from .stages import sw_library as swl
    e = swl.custom(store).get(eid) or {}
    return {"entry": eid, "duplicate_of": e.get("duplicate_of"), "variant": e.get("variant", True) is not False,
            "original": variant_of(store, eid), "built": has_build(store, eid)}
