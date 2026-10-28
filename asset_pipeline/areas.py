"""Areas: the unit of the daily asset loop. An area is a set of shots (what they show) and/or
districts (every entry built there); batch commands take --area NAME or --district ID, skip
approved assets, and pick up where they stopped.

    ap area define PROJECT NAME --shots S1 S2 ... [--districts D ...]
    ap area status PROJECT (NAME | --district D)       what each asset needs next
    ap area checklist PROJECT NAME                     the checklist in the project (areas/NAME.md)

Membership: a library entry is in a shot-defined area when one of the shots' id passes shows a
slot of it (at least MIN_SHARE of the frame; tile slots through the entries that list them), and
in a district-defined one when the entry has instances in that district.
"""
import hashlib
import re
from pathlib import Path

from .project import ProjectStore, read_json, write_json

MIN_SHARE = 0.0005


def path(store: ProjectStore) -> Path:
    from .stages import sw_site
    return sw_site.site_dir(store) / "areas.json"


def load(store: ProjectStore) -> dict:
    return read_json(path(store), default={}) or {}


def define(store: ProjectStore, name: str, shots: list[str] | None = None, districts: list[str] | None = None) -> dict:
    from .stages import sw_site
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise ValueError(f"area name {name!r}: lower-case letters, digits and dashes")
    known = {s["id"] for s in sw_site.load_greybox(store)["shots"]}
    bad = [s for s in shots or [] if s not in known]
    if bad:
        raise ValueError(f"no shot(s) {', '.join(bad)} (ap shots list {store.root.name})")
    if not shots and not districts:
        raise ValueError("give --shots and/or --districts")
    a = load(store)
    a[name] = {"shots": shots or [], "districts": districts or []}
    write_json(path(store), a)
    return a[name]


def brief_sha(store: ProjectStore, asset: str) -> str | None:
    from . import kit_proc
    sp = kit_proc.spec_path(store, asset)
    return hashlib.sha1(sp.read_bytes()).hexdigest()[:12] if sp.is_file() else None


def _entries_and_decisions(store):
    from .stages import sw_library
    data = sw_library.load(store)
    if data is None:
        raise FileNotFoundError(f"no library plan yet: run ap library plan {store.root.name}")
    ents = {e["id"]: e for e in data["entries"]} | {u["id"]: u for u in data.get("unseen", [])}
    return ents, sw_library.decisions(store)


def members(store: ProjectStore, area: str | None = None, district: str | None = None,
            everywhere: bool = False) -> dict[str, float]:
    """{entry: its largest frame share in the area's shots (0 for district-only members)}.
    everywhere: every library entry, whatever area it is in (its largest share in any shot)."""
    from .stages import sw_shots, sw_site
    if everywhere:
        ents, _ = _entries_and_decisions(store)
        return {k: max([float(s.get("share") or 0.0) for s in e.get("sources") or []] + [float(e.get("share") or 0.0)])
                for k, e in ents.items()}
    if not area and not district:
        raise ValueError("give an area (--area NAME) or a district (--district ID)")
    spec = {"shots": [], "districts": [district]} if district else load(store).get(area)
    if spec is None:
        raise KeyError(f"no area {area!r}: define it with ap area define {store.root.name} {area} --shots ...")
    ents, _ = _entries_and_decisions(store)
    by_slot = {}
    for e in ents.values():
        for sid in e.get("slot_ids") or []:
            by_slot.setdefault(sid, []).append(e["id"])
    slots = {s["id"]: s for s in sw_site.load_greybox(store)["slots"]} if spec["shots"] else {}   # districts only: no greybox needed
    out = {}
    for shot in spec["shots"]:
        ids = read_json(sw_shots.shot_dir(store, shot) / "ids.json", default=None)
        if ids is None:
            raise FileNotFoundError(f"{shot}: no id pass; run ap shots render {store.root.name} {shot}")
        for sid, v in ids["slots"].items():
            if v["frac"] < MIN_SHARE:
                continue
            got = by_slot.get(sid, [])
            s = slots.get(sid) or {}
            if not got and s.get("typology") not in (None, "None"):     # entries newer than the plan
                t = s["typology"].replace("_", "-")
                got = [k for k in (f"{t}-{s.get('material')}", t) if k in ents][:1]
            for e in got:
                out[e] = max(out.get(e, 0.0), v["frac"])
    for e in ents.values():                          # props and kit pieces inside a slot (stalls in a market):
        for src in e.get("sources") or []:           # the shots the library plan found them in
            if src.get("shot") in spec["shots"]:
                out[e["id"]] = max(out.get(e["id"], 0.0), float(src.get("share") or 0.0))
    for d in spec.get("districts") or []:
        for e in ents.values():
            if d in (e.get("districts") or {}) or e.get("district") == d:
                out.setdefault(e["id"], 0.0)
    from . import variants                           # a variant set's members go where its base goes
    for base, vs in variants.sets(store).items():
        if base in out and (not vs.get("areas") or not area or area in vs["areas"]):
            for m in vs.get("members") or {}:
                if m in ents:
                    out.setdefault(m, out[base])
    dups = {k: e["duplicate_of"] for k, e in ents.items() if e.get("duplicate_of")}
    while True:                                      # duplicates (and theirs) go where their original goes
        new = {k: out[o] for k, o in dups.items() if o in out and k not in out}
        if not new:
            return out
        out.update(new)


def slots_in(store: ProjectStore, area: str) -> set[str]:
    """The greybox slots an area covers: those its shots show (MIN_SHARE of a frame or more) and
    those in its districts."""
    from .stages import sw_shots, sw_site
    spec = load(store).get(area)
    if spec is None:
        raise KeyError(f"no area {area!r}")
    out = set()
    for shot in spec["shots"]:
        ids = read_json(sw_shots.shot_dir(store, shot) / "ids.json", default=None) or {"slots": {}}
        out |= {sid for sid, v in ids["slots"].items() if v["frac"] >= MIN_SHARE}
    ds = set(spec.get("districts") or [])
    if ds:
        out |= {s["id"] for s in sw_site.load_greybox(store)["slots"] if s.get("district") in ds}
    return out


def assets_of(store: ProjectStore, entry: str, decision: dict | None) -> list[str]:
    """The assets an entry is made of: its own, or its kit modules (decision kit)."""
    if (decision or {}).get("decision") == "kit":
        return [f"{entry}-{k}" for k in (decision.get("pieces") or {})]
    return [entry]


def status(store: ProjectStore, area: str | None = None, district: str | None = None,
           everywhere: bool = False) -> list[dict]:
    """One row per asset of the area (everywhere: of the whole library): what it has (sheets, brief,
    build, review) and its next step."""
    from . import kit_proc, library_review, variants
    from .stages import s2_refs, s5_3d, sw_library
    ents, dec = _entries_and_decisions(store)
    in_set = {m: base for base, vs in variants.sets(store).items() for m in (vs.get("members") or {}) if m != base}
    in_set |= variants.duplicates_as_variants(store)
    revs = library_review.reviews(store)
    copies = variants.duplicates_as_variants(store)
    rows = []
    for eid, share in sorted(members(store, area, district, everywhere).items(), key=lambda kv: -kv[1]):
        e, d = ents[eid], dec.get(eid) or {}
        if e.get("kind") == "vegetation":
            continue                                   # trees: Unreal PCG (vegetation zones, street-tree slots)
        route = sw_library.route(e, d) or "trellis"
        for a in assets_of(store, eid, d):
            nsheets = len(s2_refs.sheets_of(store, sw_library.PLAN_NAME, a))       # on disk: included in the plan or not
            brief = kit_proc.spec_path(store, a).is_file()
            rep = read_json(kit_proc.out_dir(store, a) / "procedural.json", default=None)
            built = (kit_proc.out_dir(store, a) / "procedural.glb").is_file()
            fresh = built and rep is not None and rep.get("brief_sha") == brief_sha(store, a)
            inst = read_json(kit_proc.out_dir(store, a) / "instances" / "index.json", default=None) or []
            trellis = s5_3d.results(store, sw_library.PLAN_NAME, a) if route == "trellis" else []
            st = (revs.get(a) or {}).get("status")
            missing = []
            if route == "procedural" and not brief and e.get("kind") != "kit":
                from . import library_entries
                fi = library_entries.form_info(store, a)
                missing = [k for k, v in (("form", fi["form"]), ("size", fi["size_m"])) if not v]
            row = {"asset": a, "entry": eid, "tier": e.get("tier"), "kind": e.get("kind"), "share": round(share, 4), "missing": missing,
                   "decision": d.get("decision"), "route": route, "sheets": nsheets, "brief": brief,
                   "built": built, "fresh": fresh, "instances": len(inst), "trellis": len(trellis),
                   "status": st, "note": (revs.get(a) or {}).get("note", ""), "variant_of": in_set.get(eid),
                   # a copy made as a variant of a decided original rides on the original's sheet
                   "copy_of": copies.get(eid) if (dec.get(copies.get(eid)) or {}).get("decision") not in (None, "exclude") else None,
                   # a good-source decision no shot frame shows can't be planned (sync_plan leaves it out)
                   "no_frame": not sw_library.frame_ok(store, e, d)}
            row["next"] = next_step(store, row, area, district)
            rows.append(row)
    return rows


SHEET_SKIP = {
    "undecided": "not decided yet (Library › Entries)",
    "excluded": "excluded",
    "approved": "already approved",
    "sheets": "already has sheets",
    "built": "already has a 3D model",
    "brief": "a procedural brief stands in for a sheet",
    "copy": "a copy shares its original's sheet",
    "no frame": "marked good source, but no shot frame shows it (decide Description or Reference)",
}


def sheet_skip(r: dict) -> str | None:
    """The one definition of "needs a reference sheet", for a `status` row: None when the asset
    does (it will be built, from a decision that isn't Exclude, and has no sheet), else why not (a
    key of SHEET_SKIP). A Description only asset with no shot frame needs one like any other: its
    sheet is drawn from the description. Used by the Library step strip (2 Sheets), the sheets
    batch (Make sheets for all), the Reference sheets tab (No sheets yet, Generate missing) and
    `ap library sheets`, so they always agree."""
    if not r["decision"]:
        return "undecided"
    if r["decision"] == "exclude":
        return "excluded"
    if r["status"] == "approved":
        return "approved"
    if r["sheets"]:
        return "sheets"
    if r["built"] or r["trellis"]:
        return "built"
    if r["route"] == "procedural" and r["brief"]:
        return "brief"
    if r.get("copy_of"):
        return "copy"
    if r.get("no_frame"):
        return "no frame"
    return None


def sheet_rows(store: ProjectStore, rows: list[dict] | None = None) -> list[dict]:
    """The `status` rows (default: the whole library) of the assets that need a reference sheet."""
    rows = status(store, everywhere=True) if rows is None else rows
    return [r for r in rows if sheet_skip(r) is None]


def sheet_states(store: ProjectStore) -> dict[str, str | None]:
    """{asset: None if it needs a sheet, else why not} for every library asset (vegetation is
    Unreal foliage and has none). {} before the library plan exists."""
    try:
        return {r["asset"]: sheet_skip(r) for r in status(store, everywhere=True)}
    except FileNotFoundError:
        return {}


STEPS = ("decide", "sheets", "star", "build", "approve")
STEP_LABELS = {"decide": "Decide: not decided yet", "sheets": "Sheets: need reference sheets",
               "star": "Choose Reference: sheets, none starred", "build": "Build: need a 3D model",
               "approve": "Approve: awaiting approval"}


def not_in_layout(store: ProjectStore) -> dict[str, dict]:
    """{entry: {reason, appears}} for the entries with no slot, building or prop piece of their own
    (next_build.explain), excluded ones included. appears: a kit's modules, attached pieces and variants still
    show in the city; a building type with no share or slots never does. {} before the greybox exists."""
    from . import next_build
    ex = next_build.explain(store)
    return {r["entry"]: {"reason": r["outside_reason"], "appears": r["appears"]}
            for r in ex["assets"] if ex["greybox"] and r["role"] == "none"}


def unplaced_entries(store: ProjectStore) -> set[str]:
    """The entries the next city build never places: not_in_layout and not appearing some other way. Empty
    before the greybox exists: then nothing is known to be left out."""
    return {e for e, v in not_in_layout(store).items() if not v["appears"]}


def library_status(store: ProjectStore) -> dict:
    """The one definition of where every library asset waits, for the Library step strip, its banner,
    the Generate 3D Model & Review tab and its status line, and `ap library status`:
      steps    {asset: decide | sheets | star | build | approve | None}; sheets is areas.sheet_skip, and
               an asset the next city build doesn't place (outside) waits at neither build nor approve;
      needs_views, needs_model, awaiting_approval: asset lists (needs_model = step build, awaiting_approval
               = step approve, needs_views = at build with a starred sheet nothing was cut from);
      outside  {asset: why} left out of those counts (never appear in the city);
      not_in_layout  {asset: {reason, appears}}: every asset with no slot of its own, appearing ones included;
      counts   the lists' lengths, plus assets (the decided, built-or-to-be-built ones that are placed),
               with_views and with_model among them.
    {} lists before the library plan exists. Vegetation is Unreal foliage and never waits."""
    from . import review, versions
    from .stages import s3_views, sw_library
    empty = {"steps": {}, "needs_views": [], "needs_model": [], "awaiting_approval": [], "outside": {}, "not_in_layout": {},
             "counts": dict.fromkeys(("assets", "with_views", "with_model", "needs_views", "needs_model", "awaiting_approval"), 0)}
    try:
        rows = status(store, everywhere=True)
    except FileNotFoundError:
        return empty
    layout = not_in_layout(store)
    unplaced = {e for e, v in layout.items() if not v["appears"]}
    starred = {}
    for k in review.starred(store, "refs/library/"):
        parts = Path(k).parts
        if len(parts) == 4:
            starred.setdefault(parts[2], []).append(k)
    steps, outside, listed, needs_views, pop, with_views, with_model = {}, {}, {}, [], [], 0, 0
    for r in rows:
        a = r["asset"]
        model = bool(r["built"] or r["trellis"] or (versions.has_bank(store, a) and versions.current(store, a) is not None))
        skip = sheet_skip(r)
        if not r["decision"]:
            step = "decide"
        elif r["decision"] == "exclude" or r["status"] == "approved":
            step = None
        elif skip is None:
            step = "sheets"
        elif model:
            step = "approve"
        elif not r["sheets"]:
            step = "build" if skip == "brief" else None
        else:
            step = "build" if starred.get(a) else "star"
        lay = layout.get(r["entry"])
        if lay:                                         # listed with its reason; only one that never appears is left out
            why = f"kit module of {r['entry']}" if a != r["entry"] and r["decision"] == "kit" else lay["reason"]
            listed[a] = {"reason": why, "appears": lay["appears"]}
        if step in ("build", "approve") and r["entry"] in unplaced:
            step, outside[a] = None, listed[a]["reason"]
        elif r["decision"] and r["decision"] != "exclude" and r["entry"] in unplaced:
            outside[a] = listed[a]["reason"]
        steps[a] = step
        if r["decision"] and r["decision"] != "exclude" and a not in outside:
            pop.append(a)
            with_views += bool(s3_views.views(store, sw_library.PLAN_NAME, a))
            with_model += model
        if step == "build":
            cut = {v["sheet"] for v in s3_views.views(store, sw_library.PLAN_NAME, a)}
            if any(k not in cut for k in starred.get(a, [])):
                needs_views.append(a)
    lists = {"needs_model": sorted(a for a, s in steps.items() if s == "build"),
             "awaiting_approval": sorted(a for a, s in steps.items() if s == "approve")}
    return {"steps": steps, "needs_views": sorted(needs_views), **lists, "outside": outside, "not_in_layout": listed,
            "counts": {"assets": len(pop), "with_views": with_views, "with_model": with_model,
                       "needs_views": len(needs_views), **{k: len(v) for k, v in lists.items()}}}


def next_step(store: ProjectStore, r: dict, area: str | None, district: str | None) -> str:
    sel = f"--area {area}" if area else f"--district {district}" if district else "--area NAME"
    p = store.root.name
    if r["status"] == "approved":
        return "done (approved)"
    if not r["decision"] and not r["brief"]:
        return f"decide its source in Library › Entries (or ap library decide {p} {r['entry']} ...)"
    skip = sheet_skip(r)
    if skip is None:
        return f"make sheets: References tab, or ap library sheets {p} {sel}"
    if skip == "no frame":
        return (f"its Frame crop is gone (no shot frame shows it): decide Description or Reference in Library › Entries "
                f"(or ap library decide {p} {r['entry']} ...)")
    if r["route"] == "procedural":
        if r.get("missing"):
            return (f"set its {' and '.join(r['missing'])}: Manage entry… › Form / Size in Library › Asset review "
                    f"(the Brief & build dialog asks too)")
        if not r["brief"]:
            return f"brief from the best sheet view: ap library brief {p} {r['asset']} <sheet> front"
        if not r["built"] or not r["fresh"]:
            return f"build: Library › Asset review › Save & Rebuild, or ap library build {p} {sel}"
        if r["status"] in ("needs_changes", "needs_feature"):
            return "match the sheet with brief fields, then Save & Rebuild (RUNBOOK: matching a sheet)"
        return "review: Library › Asset review (A approve, C needs changes)"
    if not r["trellis"]:
        return f"pick a view in Review, then 3D there or ap library trellis {p} {sel}"
    return "review the TRELLIS result in Review, then Approve it there"


def module_size(store: ProjectStore, e: dict) -> list[float] | None:
    """A structure built from repeated modules, as one module: a city wall run as one wall bay
    (city.WALL_BAY_M along it, the enclosure's wall depth and height). None for anything else."""
    if (e or {}).get("typology") != "wall":
        return None
    from . import city
    from .stages import sw_site
    try:
        enc = city.with_preset(sw_site.load_layout(store)).city.enclosure
    except (FileNotFoundError, AttributeError):
        return None
    if enc is None or enc.kind != "wall":
        return None
    return [city.WALL_BAY_M, float(enc.wall_d), float(enc.wall_h)]


def _size_text(s) -> str:
    return " × ".join(f"{x:g}" for x in s) + " m"


def form_gaps(store: ProjectStore, rows: list[dict]) -> list[dict]:
    """The area's procedural assets that can't be briefed yet (no form, or no size), each with its
    suggested fixes, the first one recommended: [{asset, entry, missing, size_m, fixes: [{kind:
    form | route | manage, label, why, form?, size_m?, route?}]}]. A form fix sets the entry's form
    and size (Manage entry… › Form, Size); a route fix moves it to TRELLIS."""
    from . import library_entries
    from .building_gen import FORM_CHOICE
    out = []
    for r in rows:
        if r["route"] != "procedural" or r["brief"] or not r.get("missing") or r["status"] == "approved":
            continue
        a = r["asset"]
        ent, own = library_entries._entry_of(store, a)
        e = (ent or {}) if own else {}
        info = library_entries.form_info(store, a)
        choice, matched = library_entries.suggestion(e or ent or {"id": a}, None if own else a)
        size, size_why = info["size_m"], {"yours": "your size", "plan": "the plan's size",
                                         "kit piece": "the kit piece's size"}.get(info["size_source"])
        mod = module_size(store, e)
        if mod:
            size, size_why = mod, "one wall bay (the plan's size is the whole wall run)"
            matched_why = "a city wall is a run of arched bays: one bay, arches on piers with nothing over them"
        else:
            matched_why = None
        fixes = []
        if "form" in r["missing"]:
            form_fix = {"kind": "form", "form": choice, "size_m": size,
                        "label": f"Form: {FORM_CHOICE[choice]['label']}" + (f", {_size_text(size)}" if size else ""),
                        "why": (matched_why or f"its words point to it ({FORM_CHOICE[choice]['desc']})" if matched else
                                "no form matches its description; the cube is the catch-all")
                        + (f"; size: {size_why}" if size else "")}
            if matched or (e or ent or {}).get("kind") != "building":
                fixes.append(form_fix)
            else:                                       # an irregular building: TRELLIS from a sheet view
                fixes += [{"kind": "route", "route": "trellis", "label": "Build with TRELLIS",
                           "why": "no building form matches its description: TRELLIS makes it from a sheet view"},
                          form_fix]
        if not size or ("size" in r["missing"] and not fixes):
            fixes.append({"kind": "manage", "label": "Set size…", "why": "no size in the plan: give width, depth, height"})
        out.append({"asset": a, "entry": r["entry"], "missing": r["missing"], "size_m": info["size_m"], "fixes": fixes})
    return out


CHECK_BEGIN, CHECK_END = "<!-- ap area status: begin -->", "<!-- ap area status: end -->"


def checklist(store: ProjectStore, name: str) -> Path:
    """areas/NAME.md in the project: the status table (regenerated between the markers) and
    whatever the artist wrote around it (gap groups, notes), kept as it is."""
    rows = status(store, area=name)
    yes = lambda b: "yes" if b else "–"                 # noqa: E731
    table = ["| Asset | Tier | Route | Share | Sheets | Brief | Build | Review | Next |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        build = ("fresh" if r["fresh"] else "stale") if r["built"] else ("TRELLIS" if r["trellis"] else "–")
        table.append(f"| {r['asset']} | {r['tier'] or ''} | {r['route']} | {100 * r['share']:.1f}% | {r['sheets']} | {yes(r['brief'])} "
                     f"| {build} | {r['status'] or 'not reviewed'} | {r['next']} |")
    block = "\n".join([CHECK_BEGIN, "", *table, "", CHECK_END])
    out = store.root / "areas" / f"{name}.md"
    out.parent.mkdir(exist_ok=True)
    if out.is_file() and CHECK_BEGIN in out.read_text():
        txt = out.read_text()
        txt = txt[:txt.index(CHECK_BEGIN)] + block + txt[txt.index(CHECK_END) + len(CHECK_END):]
    else:
        spec = load(store)[name]
        txt = (f"# Area {name}\n\nShots: {', '.join(spec['shots']) or '–'}. Districts: {', '.join(spec['districts']) or '–'}.\n"
               f"Regenerate the table with `ap area checklist {store.root.name} {name}`; text outside it is kept.\n\n"
               f"## Status\n\n{block}\n\n## Gaps against the sheets\n\n(a) existing brief fields, (b) a new brief field, "
               f"(c) a new builder part.\n")
    out.write_text(txt)
    return out
