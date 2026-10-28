"""What the next city build places for each library asset, and why: the one source of truth for
`ap ue build` (ue_build -> s7_export(assets=True) -> city_library.plan) and its explanation in the
UI (Views and 3D's line per asset, the Unreal tab's Ready for Unreal box).

city_library takes its choices from here, so the explanation can't disagree with the build:
  - gate(store, use_active): which entries may stand in the city (Approved in library review, or
    any built entry with "Use Active versions");
  - building_model(store, entry): the model a building placement takes when the entry's Active
    version isn't procedural (a TRELLIS model): the raw model, or its Clean up output for the
    asset's tier only when the user chose it (Use this version on the cleaned card;
    city_library.active_model). Clean up is optional and never picked automatically;
  - prop_model(store, entry): the model a prop takes: the raw model, or the chosen Clean up
    output (city_library.prop_placements);
  - piece_model(store, entry): the model an attached piece takes (a gate or statue attached to a
    portico): the same as a prop (assembly.layout);
  - placed_glb(store, entry): the Active version's raw model (a parent's own frame and footprint,
    assembly.model);
  - procedural builds stand as they are on disk (look._candidates): procedural_build(store, entry)
    names the version that build is.
explain(store, use_active) puts these together per entry: placed or not, the version, why it,
its cleanup state for the asset's tier (game, cine, hero; chosen or raw), approved or not, else the stand-in
reason, and the plain line the UI shows.
"""
from pathlib import Path

from .project import ProjectStore, read_json

LIB = "3d/library"


# --- the choices city_library uses -------------------------------------------------------------

def gate(store: ProjectStore, use_active: bool):
    """ok(entry): may it stand in the city? Approved in library review, or (use_active) any built entry."""
    if use_active:
        return lambda e: True
    from .look import approved
    good = approved(store)
    return lambda e: e in good


def placed_glb(store: ProjectStore, entry: str) -> Path | None:
    """The model an attached piece places: the Active version's model (raw)."""
    from . import versions
    return versions.current_glb(store, entry)


def prop_model(store: ProjectStore, entry: str) -> dict | None:
    """The model a prop places: {n, kind, glb (Path), raw (project-relative), cleaned ({usage, glb,
    faces, method} or None), available}: the Active version's raw model, or its Clean up output
    for the asset's tier only when the user chose it; None without an Active model."""
    from . import versions
    from .stages import s6_cleanup
    g = placed_glb(store, entry)
    if g is None:
        return None
    n = versions.current(store, entry)
    raw = g.relative_to(store.root).as_posix()
    cleaned = s6_cleanup.chosen(store, versions.PLAN, entry, raw)
    return {"n": n, "kind": versions.get(store, entry, n)["kind"] if n is not None else None,
            "glb": store.root / cleaned["glb"] if cleaned else g, "raw": raw, "cleaned": cleaned,
            "available": s6_cleanup.for_source(store, versions.PLAN, entry, raw) is not None}


def piece_model(store: ProjectStore, entry: str) -> dict | None:
    """The model an attached piece places: as prop_model (raw unless a Clean up output was chosen)."""
    return prop_model(store, entry)


def building_model(store: ProjectStore, entry: str) -> dict | None:
    """An entry's Active version when it isn't a procedural build (a TRELLIS model):
    {n, kind, glb (project-relative), raw, cleaned}; None when it is procedural or has no model.
    glb is the raw model, or the version's Clean up output for the asset's tier when the user
    chose it; cleaned says which ({usage, faces, method} or None); available: a Clean up output
    of this model exists (chosen or not)."""
    from . import versions
    from .stages import s6_cleanup
    n = versions.current(store, entry)
    if n is None:
        return None
    kind = versions.get(store, entry, n)["kind"]
    glb = versions.current_glb(store, entry) if kind != "procedural" else None
    if glb is None:
        return None
    raw = glb.relative_to(store.root).as_posix()
    cleaned = s6_cleanup.chosen(store, versions.PLAN, entry, raw)
    return {"n": n, "kind": kind, "glb": cleaned["glb"] if cleaned else raw, "raw": raw, "cleaned": cleaned,
            "available": s6_cleanup.for_source(store, versions.PLAN, entry, raw) is not None}


def procedural_build(store: ProjectStore, entry: str) -> dict | None:
    """The entry's procedural build on disk, as building placements take it (procedural.glb with
    its job.json): {glb, n (the version it is, None when no version matches)}; None without one."""
    from . import versions
    d = store.root / LIB / entry
    glb, job = d / "procedural.glb", d / "procedural" / "job.json"
    if not (glb.is_file() and job.is_file()):
        return None
    n = None
    if versions.has_bank(store, entry):
        cur = versions.current(store, entry)
        for r in sorted(versions.load(store, entry), key=lambda r: (r["n"] != cur, -r["n"])):   # the Active one wins a shared build
            g = versions.version_glb(store, entry, r)
            if r["kind"] == "procedural" and g is not None and g.resolve() == glb.resolve():
                n = r["n"]
                break
    return {"glb": glb.relative_to(store.root).as_posix(), "n": n}


# --- the roles an entry plays in the city ----------------------------------------------------

def _prop_entries(store: ProjectStore, gb: dict) -> set[str]:
    """Entries standing on greybox prop pieces (city_library.prop_placements), variants included."""
    from . import variants
    from .city_library import PROP_MAINS, prop_groups
    from .stages.sw_library import PROP_PIECES, _slug
    out = set()
    for s in gb["slots"]:
        for _, noun, _ in prop_groups(s, PROP_PIECES, PROP_MAINS):
            out.add(_slug(noun))
    for keys in variants.assignments(store, gb).values():
        out.update(keys.values())
    for o, sp in variants.splits(store).items():          # variants with no model yet: they'd share these places
        if o in out:
            out.update(sp.get("waiting") or [])
    return out


def _building_entries(store: ProjectStore, modelled: set[str] | None = None) -> set[str]:
    """Entries with building placements: instance builds of typed buildings, slots of their own,
    or planned buildings their Active model (TRELLIS) stands at (_modelled_entries)."""
    lib = store.root / LIB
    out = {p.parent.parent.name for p in lib.glob("*/instances/index.json")}
    from .stages import sw_library
    for ent in sw_library.claims(store):
        if ent.get("slot_ids"):
            out.add(ent["id"])
    return out | (_modelled_entries(store) if modelled is None else modelled)


def _modelled_entries(store: ProjectStore) -> set[str]:
    """Entries of planned typed buildings whose Active version is a model, not a procedural build:
    it stands at those buildings with no instance builds (city_library.model_placements)."""
    from . import city_library, library_entries, library_instances
    try:
        P = city_library.city_plan(store)
    except FileNotFoundError:
        return set()
    ents = set(library_entries._all_entries(store))
    from .stages import sw_library
    stand = sw_library.stand_ins(store)
    planned = {library_instances.entry_of(b, ents) for b in P.typed} - {None}
    planned |= {stand[e] for e in planned if e in stand}                       # (an excluded entry's stand-in)
    planned |= {c["id"] for c in sw_library.claims(store) if c.get("slot_ids")}      # (a slot's own: model_slot_placements)
    return {e for e in planned if building_model(store, e) is not None}


# --- the explanation -------------------------------------------------------------------------

def explain(store: ProjectStore, use_active: bool = False) -> dict:
    """Per library entry, what the next city build places: {use_active, greybox, assets: [{entry,
    name, role (prop | building | none), placed, version, kind, why, active, approved,
    approved_version, usage, cleaned, cleanup_used, model (cleaned | raw | procedural | None),
    stand_in (reason or None), excluded, line}], counts}."""
    from . import library_entries as le, versions
    from .look import approved as approved_set
    from .stages import s6_cleanup, sw_library, sw_site
    has_gb = (sw_site.site_dir(store) / "greybox.json").is_file()
    gb = sw_site.load_greybox(store) if has_gb else {"slots": []}
    props = _prop_entries(store, gb) if has_gb else set()
    modelled = _modelled_entries(store) if has_gb else set()
    buildings = _building_entries(store, modelled) if has_gb else set()
    ok, good = gate(store, use_active), approved_set(store)
    dec = sw_library.decisions(store)
    rel = _relations(store, dec)
    rows = []
    for eid, ent in sorted(le._all_entries(store).items()):
        r = _row(store, eid, ent, eid in props, eid in buildings, has_gb, ok(eid), eid in good, use_active,
                 (dec.get(eid) or {}).get("decision") == "exclude", versions, s6_cleanup, eid in modelled)
        if has_gb and r["role"] == "none":            # why no slot, building or prop piece uses it, and whether it shows anyway
            r["outside_reason"], r["appears"] = outside_reason(eid, ent, dec.get(eid), rel)
            if not r["excluded"] and not r["placed"]:
                r["stand_in"] = f"not in the city layout ({r['outside_reason']})"
                r["line"] = line(r)
        rows.append(r)
    return {"use_active": use_active, "greybox": has_gb, "assets": rows, "counts": counts(rows)}


def _relations(store, dec: dict) -> dict:
    """{variant: original} (variant sets and duplicates used as variants) and {child: parent} (attached pieces)."""
    from . import variants
    var = {m: base for base, vs in variants.sets(store).items() for m in (vs.get("members") or {}) if m != base}
    var |= variants.duplicates_as_variants(store)
    att = {c["entry"]: p for p, d in dec.items() if not p.startswith("_")
           for c in (d or {}).get("children") or [] if c.get("entry")}
    return {"variant": var, "attached": att}


def outside_reason(eid: str, ent: dict, decision: dict | None, rel: dict) -> tuple[str, bool]:
    """Why an entry has no slot, building or prop piece of its own, and whether it appears in the city anyway:
    (reason, appears). A kit's modules, attached pieces and variants appear (on the buildings, with the
    parent, in the original's places); only an entry with no share and no slots never does."""
    d = (decision or {}).get("decision")
    if d == "exclude":
        return "excluded", False
    if d == "kit":
        return "a facade kit: its modules stand on buildings", True
    if rel["attached"].get(eid):
        return f"attached to {rel['attached'][eid]}", True
    if rel["variant"].get(eid):
        return f"variant of {rel['variant'][eid]}", True
    what = "building type" if ent.get("kind") == "building" else f"{ent.get('kind') or 'entry'}"
    return f"{what} with no share or slots", False


def _row(store, eid, ent, prop, building, has_gb, ok, is_approved, use_active, excluded, versions, s6_cleanup,
         modelled: bool = False) -> dict:
    cur = versions.current(store, eid) if versions.has_bank(store, eid) else None
    rows = versions.load(store, eid) if versions.has_bank(store, eid) else []
    appr_v = next((r["n"] for r in rows if r.get("approved")), None)
    usage = versions._usage(store, eid)
    out = {"entry": eid, "name": ent.get("name") or eid, "role": "prop" if prop else "building" if building else "none",
           "placed": False, "version": None, "kind": None, "why": "", "active": cur, "approved": is_approved,
           "approved_version": appr_v, "usage": usage, "cleaned": None, "cleanup_used": False, "cleanup_available": False, "model": None,
           "stand_in": None, "excluded": excluded}
    model = None
    if prop:
        m = prop_model(store, eid)
        if m is not None:
            model = {"n": m["n"], "kind": m["kind"], "model": "cleaned" if m["cleaned"] else "raw",
                     "cleaned": m["cleaned"] is not None, "used": True,
                     "available": m["available"]}
    elif building:
        m = building_model(store, eid)
        pb = procedural_build(store, eid)
        if m is not None and (pb is not None or modelled):    # the Active TRELLIS model takes the placements
            model = {"n": m["n"], "kind": m["kind"], "model": "cleaned" if m["cleaned"] else "raw",
                     "cleaned": m["cleaned"] is not None, "used": True,
                     "available": m["available"]}
        elif pb is not None:
            model = {"n": pb["n"], "kind": "procedural", "model": "procedural", "cleaned": None, "used": False, "available": False}
    if model is not None and ok:
        out |= {"placed": True, "version": model["n"], "kind": model["kind"], "model": model["model"],
                "cleaned": model["cleaned"], "cleanup_used": model["used"], "cleanup_available": model["available"]}
        why = [("Active" if model["n"] == cur else "the procedural build on disk" + (f" (Active is v{cur})" if cur else ""))
               if model["n"] is not None else "the procedural build on disk (no version)"]
        why.append("Use Active versions is on" if use_active and not is_approved else "the asset is Approved")
        if is_approved and appr_v is not None and model["n"] is not None and appr_v != model["n"]:
            why.append(f"approved as v{appr_v}")
        out["why"] = "; ".join(why)
    elif not has_gb:
        out["stand_in"] = "no greybox yet"
    elif not (prop or building):
        out["stand_in"] = "excluded" if excluded else "not in the city layout (no slot, building or prop piece uses it)"   # explain() adds the reason
    elif excluded and model is None:
        out["stand_in"] = "excluded"
    elif model is None:
        out["stand_in"] = "no 3D model yet" if prop or not cur else "no procedural build on disk for its places"
    else:
        out["stand_in"] = "not approved; only approved assets are placed"
        out |= {"version": model["n"], "kind": model["kind"], "model": model["model"], "cleaned": model["cleaned"],
                "cleanup_used": model["used"], "cleanup_available": model["available"]}
    out["line"] = line(out)
    return out


def line(r: dict) -> str:
    """The plain line per asset: "Next city build uses: v4 · cleaned for game · approved" or
    "Next city build: greybox stand-in (no 3D model yet)"."""
    if not r["placed"]:
        return f"Next city build: greybox stand-in ({r['stand_in']})"
    bits = [f"v{r['version']}" if r["version"] is not None else "the procedural build on disk"]
    tier = {"cine": "cinematic"}.get(r["usage"], r["usage"]) or "its tier"      # as the UI names the tiers
    if r["model"] == "cleaned":
        bits.append(f"cleaned for {tier} (your choice)")
    elif r["model"] == "raw":
        bits.append(f"raw model, a cleaned version exists (not chosen)" if r.get("cleanup_available")
                    else "raw model, not cleaned (optional)")
    else:
        bits.append("procedural build")
    bits.append("approved" if r["approved"] else "not approved (Use Active versions)")
    return "Next city build uses: " + " · ".join(bits)


def counts(rows: list[dict]) -> dict:
    """The readiness counts: approved and placed, Active but not approved (placed only with Use
    Active versions), stand-ins (in the city, greybox stays). Clean up is optional: an uncleaned
    asset is not a problem."""
    city = [r for r in rows if r["role"] != "none"]
    return {"approved": sum(1 for r in city if r["placed"] and r["approved"]),
            "active_not_approved": sum(1 for r in city if r["version"] is not None and not r["approved"]),
            "stand_ins": sum(1 for r in city if not r["placed"])}
