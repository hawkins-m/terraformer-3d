"""`ap`: asset pipeline command line. Stage commands are added as stages are built."""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from . import config
from .comfy.client import ComfyError, connect
from .imagegen.base import GenRequest
from .imagegen.registry import backend_for, make_backend
from .llm.base import LLMError
from .paid import PaidAPIBlocked
from .project import SLUG_RE, ProjectStore


def _size(s: str) -> tuple[int, int]:
    w, _, h = s.lower().partition("x")
    return int(w), int(h or w)


def cmd_new(a) -> None:
    """ap new my-project | ap new "Harbour Town" | ap new --name "Harbour Town": a name that isn't a
    valid folder name (or --name alone) makes the folder from the name (project.unique_slug)."""
    slug, name = a.slug, a.name or ""
    if slug and not SLUG_RE.match(slug) and not name:
        slug, name = None, slug                   # a display name given as the argument
    if not slug and not name:
        raise SystemExit("give a project name (or a folder name): ap new \"Harbour Town\"")
    store = ProjectStore.create(slug, name=name, brief=a.brief or "", start=a.start)
    print(f"created project {store.root.name} at {store.root}")


def cmd_comfy_check(a) -> None:
    c = config.backends()["comfyui"]
    stats = connect().health()
    sysinfo = stats.get("system", {})
    print(f"ComfyUI {sysinfo.get('comfyui_version', '?')} at {c['url']}")
    for d in stats.get("devices", []):
        print(f"  {d.get('name')}: {d.get('vram_free', 0) / 2**30:.1f} / "
              f"{d.get('vram_total', 0) / 2**30:.1f} GB free")


def cmd_comfy(a) -> None:
    """ap comfy start | stop | restart | status: the local ComfyUI ([comfyui.service])."""
    from .comfy import service
    if a.action == "status":
        st = service.status()
        s = service.settings()
        print(f"ComfyUI {st['state']} at {st['url']}" + (f" (pid {st['pid']}{', started by ap' if st['managed'] else ''})" if st["pid"] else ""))
        print(f"  launch: {' '.join(service.command(s))}" + (f"  (cwd {s['cwd']})" if s.get("cwd") else "")
              + (f"  GPU {s['gpu']}" if str(s.get('gpu', '')) != "" else ""))
        print(f"  log: {st['log']}")
        if "proxy" in st:
            print(f"  comfy-api-proxy ({st['backend']} backend): {'up' if st['proxy']['up'] else 'down'} at {st['proxy']['url']}")
        return
    try:
        if a.action == "stop":
            service.stop()
        else:
            fn = service.start if a.action == "start" else service.restart
            on_line = (lambda ln: print(f"  | {ln}")) if a.verbose else (
                lambda ln: print(f"  ! {ln}") if service.ERROR_RE.search(ln) else None)
            st = fn(print, on_line)
            print(f"ComfyUI {st['state']} at {st['url']}" + (f" (pid {st['pid']})" if st.get("pid") else ""))
    except RuntimeError as e:
        raise SystemExit(str(e))


def _parse_value(raw: str):
    import tomllib
    try:
        return tomllib.loads(f"v = {raw}")["v"]                # numbers, true/false, [lists], "quoted"
    except tomllib.TOMLDecodeError:
        return raw


def cmd_config(a) -> None:
    """ap config path | show | get KEY | set KEY VALUE | unset KEY | migrate ROOT"""
    from . import config
    if a.config_cmd == "path":
        print(f"config:  {config.user_toml()}{'' if config.user_toml().exists() else '  (not written yet)'}")
        print(f"secrets: {config.secrets_toml()}{'' if config.secrets_toml().exists() else '  (none)'}")
        print(f"storage: {config.ap_root()}")
    elif a.config_cmd == "show":
        eff = dict(config.backends())
        eff["paths"] = dict(eff.get("paths") or {}, ap_root=str(config.ap_root()))
        print(config.dumps(eff))
        keys = sorted(set(config.secrets()) | {k for k in config.SECRET_KEYS if os.environ.get(k)})
        print(f"# API keys set: {', '.join(keys) or 'none'} (values not shown)")
    elif a.config_cmd == "get":
        v = config.get(a.key)
        if v is None:
            raise SystemExit(f"ap: no setting {a.key}")
        print(v)
    elif a.config_cmd in ("set", "unset"):
        sec, _, key = a.key.rpartition(".")
        if not sec:
            raise SystemExit("ap: KEY is section.key, e.g. tools.blender")
        if a.config_cmd == "unset":
            print(f"wrote {config.save_user({}, remove=[a.key])}")
            return
        upd: dict = {}
        node = upd
        for part in sec.split("."):
            node = node.setdefault(part, {})
        node[key] = _parse_value(a.value)
        print(f"wrote {config.save_user(upd)}")
    elif a.config_cmd == "migrate":
        root = Path(a.root).expanduser()
        if not root.is_dir():
            raise SystemExit(f"ap: no such folder {root}")
        old = {"unreal": {k: v for k, v in (("editor_cmd", a.ue_editor_cmd), ("projects_dir", a.ue_projects),
                                            ("backup_dir", a.ue_backups)) if v}}
        path = config.migrate(root, old)
        print(f"wrote {path}" if path else f"ap: {config.user_toml()} exists already: nothing written")


def cmd_setup(a) -> None:
    """ap setup: ask for each setting with the current or detected value pre-filled, test it,
    write the user config. --yes takes the pre-filled values, --check only tests. Without any of
    those (or --terminal, --set): the setup wizard in the browser (asset_pipeline/installer.py)."""
    import getpass
    from . import setup
    if not (a.terminal or a.yes or a.check or a.set):
        from . import installer
        installer.serve(open_page=not a.no_open)
        return
    st = setup.state()
    vals = {i["id"]: i["value"] or i["detected"] for i in st["items"]}
    for kv in a.set or []:
        k, _, v = kv.partition("=")
        if k not in setup.BY_ID:
            raise SystemExit(f"ap: no setup item {k!r}: one of {', '.join(setup.BY_ID)}")
        vals[k] = v
    print(f"Terraformer setup. Config: {st['config']}" + ("  (first run)" if st["first_run"] else ""))
    if st["gpus"]:
        print("GPUs: " + "; ".join(f"{g['index']} = {g['name']} ({g['vram_gb']:.0f} GB)" for g in st["gpus"]))
    interactive = not (a.yes or a.check) and sys.stdin.isatty()
    secrets, failed = {}, []
    for it in st["items"]:
        item = setup.BY_ID[it["id"]]
        if not setup._shown(item, vals):
            continue
        print(f"\n{item.label}{' (optional)' if item.optional else ''}: {item.help}")
        if item.kind == "secret":
            if interactive:
                got = getpass.getpass(f"  {item.key.removeprefix('secret:')} ({'set; Enter keeps it' if it['has_value'] else 'Enter skips'}): ")
                if got.strip():
                    secrets[item.key.removeprefix("secret:")] = got.strip()
            r = setup.test(item.id, secrets.get(item.key.removeprefix("secret:"), ""), vals)
        else:
            while True:
                if interactive:
                    hint = f" [{', '.join(item.choices)}]" if item.choices else ""
                    got = input(f"  {item.label}{hint} [{vals.get(item.id, '')}]: ").strip()
                    if got == "-" and item.optional:
                        got = ""
                        vals[item.id] = ""
                    elif got:
                        vals[item.id] = got
                r = setup.test(item.id, vals.get(item.id, ""), vals)
                if r["ok"] or not interactive:
                    break
                print(f"  ✗ {r['message']}")
                if input("  Keep it anyway? [y/N] ").strip().lower() == "y":
                    break
        mark = "✓" if r["ok"] and not r["warn"] else ("!" if r["ok"] else "✗")
        print(f"  {mark} {r['message']}")
        if not r["ok"] and not item.optional:
            failed.append(item.label)
    if a.check:
        print("\nNothing written (--check).")
    else:
        if interactive and input("\nSave these settings? [Y/n] ").strip().lower() == "n":
            print("Nothing written.")
            return
        out = setup.save({k: v for k, v in vals.items() if setup._shown(setup.BY_ID[k], vals)}, secrets)
        print(f"\nWrote {out['config']}" + (f" and {len(secrets)} key(s) to {setup.config.secrets_toml()}" if secrets else ""))
        print(f"Storage: {out['storage']}. Next: ap doctor, then ap ui.")
    if failed:
        print(f"Still to fix: {', '.join(failed)}.")


def cmd_models(a) -> None:
    """ap models list | download | verify (the manifest: config/models.toml)."""
    import json as _json
    from . import models
    if a.models_cmd == "list":
        rows = []
        for m in models.load():
            st = models.status(m)
            rows.append({"id": m.id, "title": m.title, "need": models.need_label(m), "feature": m.feature,
                         "state": st["state"], "size": m.size, "missing_bytes": st["missing_bytes"], "gated": m.gated,
                         "license": m.license, "used_by": m.used_by, "where": st["where"], "repo": m.source().repo,
                         "vram_gb": m.vram_gb})
        if a.json:
            print(_json.dumps(rows, indent=1))
            return
        shown = [r for r in rows if a.all or r["need"] != "experimental"]
        mark = {"installed": "installed", "missing": "missing", "partial": "partial", "no_root": "no folder"}
        print(f"{'model':24} {'need':12} {'state':10} {'size':>9}  used by")
        for r in shown:
            extra = f" ({models.gb(r['size'] - r['missing_bytes'])} so far)" if r["state"] == "partial" else ""
            gate = " [gated]" if r["gated"] else ""
            print(f"{r['id']:24} {r['need']:12} {mark[r['state']]:10} {models.gb(r['size']):>9}  {r['used_by']}{gate}{extra}")
        req = [r for r in rows if r["need"] == "required"]
        miss = [r for r in req if r["state"] != "installed"]
        print(f"\nRequired: {len(req) - len(miss)} of {len(req)} installed"
              + (f"; {models.gb(sum(r['missing_bytes'] for r in miss))} to download: ap models download --required" if miss else "."))
        if any(r["state"] == "no_root" for r in shown):
            print("ComfyUI models folder not set: ap setup, or ap config set comfyui.models_dir <your ComfyUI>/models")
        if not a.all:
            print("Experimental models: ap models list --all")
        return
    picked = models.select(a.names, required_only=a.required, all_=a.all, feature=a.feature)
    ev = models.Events(as_json=a.json)
    if a.models_cmd == "verify":
        bad = models.verify([m for m in picked])
        if not a.json:
            print("all checksums match" if not bad else f"mismatch: {', '.join(bad)}")
        if bad:
            raise SystemExit(1)
        return
    res = models.download(picked, ev, dry_run=a.dry_run)
    if res["failed"]:
        raise SystemExit(1)


def cmd_doctor(a) -> None:
    """ap doctor: every check with what is missing and how to fix it; exit 1 when a required one fails."""
    import json as _json
    from . import doctor
    rep = doctor.report()
    if a.json:
        print(_json.dumps(rep, indent=1))
    else:
        for c in rep["checks"]:
            mark = {"ok": "✓", "warn": "!", "fail": "-" if c["optional"] else "✗"}[c["level"]]
            print(f"{mark} {c['name']}: {c['detail']}")
            if c["fix"]:
                print(f"    fix: {c['fix']}")
        off = [f"{v['what']} ({k})" for k, v in rep["features"].items() if not v["ok"]]
        if off:
            print("\nOff until fixed: " + "; ".join(off) + ".")
        print("\n" + (f"{rep['missing']} required piece(s) missing." if rep["missing"] else "Everything required is in place.")
              + (f" {rep['optional_missing']} optional missing." if rep["optional_missing"] else ""))
        from . import needs                          # the setup wizard's "What you still need" page, as text
        print("\n" + "\n".join(needs.lines(rep["needs"])))
    if rep["missing"]:
        raise SystemExit(1)


def cmd_preflight(a) -> None:
    from . import preflight
    checks = preflight.run(ue=a.ue)
    for c in checks:
        mark = "ok  " if c.ok else ("FAIL" if c.required else "warn")
        print(f"[{mark}] {c.name}: {c.detail}")
    failed = [c.name for c in checks if c.required and not c.ok]
    if failed:
        raise SystemExit(f"preflight failed: {', '.join(failed)}")
    print("preflight ok")


def cmd_gen(a) -> None:
    project = store = None
    if a.project:
        store = ProjectStore.open(a.project)
        project = store.load()
    backend = make_backend(a.backend) if a.backend else backend_for(a.stage, project)
    w, h = _size(a.size)
    anchor = None
    if a.anchor:
        if not (project and project.anchor):
            raise ValueError("--anchor needs --project with a saved style anchor")
        anchor = project.anchor
    req = GenRequest(prompt=a.prompt, width=w, height=h, n=a.n, seed=a.seed, steps=a.steps,
                     anchor=anchor)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = a.out or ((store.root if store else config.ap_root() / "outputs") / "gen" / stamp)
    t = time.time()
    results = backend.generate(req, Path(out))
    elapsed = round(time.time() - t, 1)
    if store:
        store.log_run({"stage": "gen", "backend": backend.name, "request": req.model_dump(mode="json"),
                       "outputs": [str(r.path) for r in results], "seconds": elapsed})
    for r in results:
        print(r.path)
    print(f"{len(results)} image(s) via {backend.name} in {elapsed}s", file=sys.stderr)


def cmd_style_explore(a) -> None:
    from .stages import s0_style
    store = ProjectStore.open(a.project)
    out = s0_style.explore(store, a.brief, n=a.n, seed=a.seed)
    for img in sorted(out.glob("scene_*.png")):
        print(img)


def cmd_style_star(a) -> None:
    from . import review
    store = ProjectStore.open(a.project)
    for p in a.paths:
        if not a.unstar:
            from . import library_entries
            library_entries.ensure_in_plan(store, [x for x in [library_entries.sheet_asset(review.rel(store, p))] if x], strict=False)
        print(("unstarred " if a.unstar else "starred ") + review.set_star(store, p, not a.unstar))


def cmd_style_derive(a) -> None:
    from .stages import s0_style
    store = ProjectStore.open(a.project)
    out = s0_style.derive(store, a.scene, [n for n in a.nouns.split(",")], per_noun=a.per_noun,
                          regenerate=not a.no_redraw, style_text=a.style_text or "")
    for img in sorted(out.glob("*.png")):
        print(img)


def cmd_style_anchor(a) -> None:
    from .stages import s0_style
    store = ProjectStore.open(a.project)
    anchor = s0_style.save_anchor(store, strength=a.strength, style_text=a.style_text)
    print(f"anchor: {len(anchor.images)} image(s), strength {anchor.strength}, "
          f"style text {anchor.style_text!r}")


def cmd_style_show(a) -> None:
    from . import review
    from .stages import s0_style
    store = ProjectStore.open(a.project)
    p = store.load()
    print(f"project {p.slug}: brief {p.brief!r}")
    for b in sorted((store.root / s0_style.EXPLORE).glob("batch_*")):
        print(f"  {b.relative_to(store.root)}: {len(list(b.glob('scene_*.png')))} scenes")
    print("starred scenes:", *review.starred(store, s0_style.EXPLORE + "/") or ["(none)"], sep="\n  ")
    print("starred derived:", *review.starred(store, s0_style.DERIVE + "/") or ["(none)"], sep="\n  ")
    if p.anchor:
        print(f"anchor: {len(p.anchor.images)} image(s), strength {p.anchor.strength}, "
              f"style text {p.anchor.style_text!r}")


def cmd_plan_import(a) -> None:
    from .stages import s1_plan
    store = ProjectStore.open(a.project)
    for img in a.images:
        print(s1_plan.import_scene(store, img))


def cmd_plan_analyze(a) -> None:
    from .stages import s1_plan
    store = ProjectStore.open(a.project)
    keys = a.scenes or s1_plan.scenes(store)
    if not keys:
        raise ValueError("no scenes: star stage-0 scenes or `ap plan import` an image")
    for k in keys:
        t = time.time()
        plan = s1_plan.analyze(store, k, force=a.force)
        name = s1_plan.plan_name(plan.scene)
        print(f"{name}: {len(plan.assets)} assets, "
              f"{len(plan.relations)} relations via {plan.llm} in {time.time() - t:.0f}s")
        if not a.no_refine:
            _refine(store, name)


def _refine(store, name: str) -> None:
    from .stages import s1_plan
    t = time.time()
    try:
        plan = s1_plan.refine_boxes(store, name)
    except ComfyError as e:
        print(f"  boxes left as the LLM drew them ({e}); `ap plan refine` later")
        return
    n = sum(x.bbox_source == "sam" for x in plan.assets)
    print(f"  SAM 3.1 boxes for {n}/{len(plan.assets)} assets in {time.time() - t:.0f}s")


def cmd_plan_refine(a) -> None:
    from .stages import s1_plan
    store = ProjectStore.open(a.project)
    names = [a.name] if a.name else [n for n in s1_plan.plan_names(store) if s1_plan.load(store, n)]
    for name in names:
        print(name)
        _refine(store, name)


def cmd_plan_show(a) -> None:
    from .stages import s1_plan
    store = ProjectStore.open(a.project)
    names = [a.name] if a.name else s1_plan.plan_names(store)
    for name in names:
        plan = s1_plan.load(store, name)
        if a.json:
            print(json.dumps(plan.model_dump(mode="json") if plan else None, indent=2))
            continue
        if not plan:
            print(f"{name}: not analysed yet\n")
            continue
        print(f"{name}  ({plan.scene}, {plan.llm}{', edited' if plan.edited else ''})")
        print(f"  {plan.summary}\n  scale: {plan.scale_notes}")
        for x in plan.assets:
            d = x.dimensions
            print(f"  {'x' if x.include else '-'} {x.id:<28} {x.category:<10} x{x.count:<3} "
                  f"{d.width:g}x{d.depth:g}x{d.height:g} m  {x.usage}  box={x.bbox_source}"
                  + (f" (SAM found {x.sam_found})" if x.sam_found is not None else "")
                  + (f"  kit={x.kit}" if x.kit else ""))
        for r in plan.relations:
            print(f"    {r.subject} {r.relation} {r.object}")
        print()


def cmd_refs_generate(a) -> None:
    from .stages import s2_refs
    store = ProjectStore.open(a.project)
    if not a.unit:
        paths = s2_refs.generate_missing(store, n=a.n, plan=a.plan)
    else:
        by_key = {(u.plan, u.key): u for u in s2_refs.units(store, a.plan, pending=True)}
        paths = []
        for key in a.unit:
            matches = [u for (plan, k), u in by_key.items() if k == key]
            if not matches:
                raise ValueError(f"no unit {key!r} (see `ap refs show`)")
            s2_refs.include_pending(store, matches)
            for u in matches:
                paths += s2_refs.generate(store, u, n=a.n, seed=a.seed)
    for p in paths:
        print(p)
    if not paths:
        print("nothing to do: every unit has sheets (name units with --unit to add more)")


def cmd_refs_show(a) -> None:
    from . import review
    from .stages import s2_refs
    store = ProjectStore.open(a.project)
    stars = review.load(store)["stars"]
    for u in s2_refs.units(store, a.plan):
        sh = s2_refs.sheets(store, u)
        print(f"{u.plan}/{u.key:<32} {u.kind:<8} {len(sh)} sheet(s), "
              f"{sum(1 for k in sh if stars.get(k))} starred  [{', '.join(x.id for x in u.assets)}]")


def cmd_views_cut(a) -> None:
    from .stages import s3_views
    store = ProjectStore.open(a.project)
    if a.sheets:
        for sheet in a.sheets:
            for v in s3_views.cut(store, sheet):
                print(f"{v['file']}  asset={v['asset']} asked={v['asked']} ({v['method']})")
    else:
        print(f"{s3_views.cut_starred(store)} view(s) cut")


def cmd_review_show(a) -> None:
    from . import review
    from .stages import s1_plan, s3_views, s5_3d
    store = ProjectStore.open(a.project)
    for name in s1_plan.plan_names(store):
        plan = s1_plan.load(store, name)
        for x in (plan.assets if plan else []):
            if not x.include or x.category == "terrain":
                continue
            views = s3_views.views(store, name, x.id)
            chosen = review.chosen(store, name, x.id)
            print(f"{name}/{x.id:<30} {x.usage:<5} {len(views)} view(s)  chosen={chosen or '-'}  "
                  f"3d={len(s5_3d.results(store, name, x.id))}")


def cmd_review_choose(a) -> None:
    from . import review
    store = ProjectStore.open(a.project)
    print(review.choose(store, a.plan, a.asset, a.view))


def cmd_review_tag(a) -> None:
    from .stages import s1_plan
    store = ProjectStore.open(a.project)
    s1_plan.set_usage(store, a.plan, a.asset, a.usage)
    print(f"{a.plan}/{a.asset}: {a.usage}")


def cmd_3d(a) -> None:
    from .stages import s5_3d
    store = ProjectStore.open(a.project)
    rec = s5_3d.run(store, a.plan, a.asset, view=a.view, mode=a.mode, seed=a.seed)
    print(store.root / rec["glb"])


def cmd_hero_orbit(a) -> None:
    from .stages import s5_hero
    store = ProjectStore.open(a.project)
    out = s5_hero.orbit(store, a.plan, a.asset, seed=a.seed)
    r = s5_hero.analyse(out)
    print(out)
    print(f"drift {r['drift_frac']:.0%}, closes at frame {r['closure_frame']} "
          f"(similarity {r['closure_similarity']}), picks {r['picks'] or '-'}; contact sheet: "
          f"{out / 'contact.png'}")
    print("prototype: the picked frames' angles are approximate (see docs/notes/stages-0-6.md)")


def cmd_hero_angles(a) -> None:
    from .stages import s5_hero
    store = ProjectStore.open(a.project)
    orbit = store.root / s5_hero.HERO / a.plan / a.asset / a.orbit
    glb = s5_hero.trellis_mesh_for_chosen(store, a.plan, a.asset)
    tt = s5_hero.render_turntable(glb, orbit / "turntable")
    r = s5_hero.match_angles(orbit, tt)
    print(f"mesh {glb.name}; turned {r['rotation_deg']} deg (reach {r['max_reach_deg']}), "
          f"fit {r['mean_fit']}, sides {r['picks']}")
    print("experimental: on real Wan orbits these angles are unreliable (see docs/notes/stages-0-6.md)")


def cmd_hero_mesh(a) -> None:
    from .stages import s5_hero
    store = ProjectStore.open(a.project)
    orbit = store.root / s5_hero.HERO / a.plan / a.asset / a.orbit
    views = {side: orbit / f"frame_{getattr(a, side):03d}.png"
             for side in ("front", "left", "back", "right") if getattr(a, side) is not None}
    missing = [str(p) for p in views.values() if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"no such frame(s): {', '.join(missing)}")
    tag = "_".join(f"{k[0]}{getattr(a, k)}" for k in ("front", "left", "back", "right") if getattr(a, k) is not None)
    out = s5_hero.multiview_mesh(views, orbit / f"mv_{tag}_s{a.seed}.glb", seed=a.seed)
    print(out)


def cmd_cleanup(a) -> None:
    from .stages import s6_cleanup
    store = ProjectStore.open(a.project)
    rep = s6_cleanup.run(store, a.plan, a.asset, glb=a.glb, fit=a.fit, budget=a.budget)
    print(store.root / rep["glb"])
    print(f"{rep['usage']}: {rep['output_faces']} faces, {rep['output_dims_m']} m "
          f"(vs plan {rep['dims_vs_plan']}), {rep['seconds']} s")
    for w in rep["warnings"]:
        print(f"warning: {w}")


def _vec(s: str, n: int = 3) -> tuple[float, ...]:
    v = tuple(float(x) for x in s.split(","))
    if len(v) != n:
        raise ValueError(f"expected {n} comma-separated numbers, got {s!r}")
    return v


def cmd_site_init(a) -> None:
    from .stages import sw_site
    store = ProjectStore.open(a.project)
    layout = sw_site.init(store, Path(a.layout) if a.layout else None, force=a.force, city_mode=a.city)
    print(f"{store.root / 'site/layout.json'}: {len(layout.plots)} plots, {len(layout.rows)} ring rows, "
          f"{len(layout.shots)} shots; project is in world mode. Next: ap site build {a.project}")


def cmd_site_plan(a) -> None:
    from .stages import sw_site
    store = ProjectStore.open(a.project)
    if a.shots or a.shot:
        only = a.shot or None
        sw_site.set_city_shots(store, only)
        print("re-placed shots:", ", ".join(only) if only else "all", "(rebuild to apply)")
    st = sw_site.plan_city(store)
    kinds = (f"{len(st['repetition']['typologies'])} typologies" if st.get("repetition") else
             f"{st['housing_blocks']} in blocks/terraces, {st['houses']} houses")
    print(f"{sw_site.site_dir(store) / 'city_plan.png'}: {st['buildings']} buildings ({kinds}), "
          f"{st['blocks']} blocks, {st['streets']} streets, "
          f"{st['parks']} parks ({st['park_area_ha']} ha), {st['markets']} markets, {st['trees']} trees")
    print("footprint share:", ", ".join(f"{k} {v:.1%}" for k, v in st["footprint_share"].items()),
          f"; heights median {st['height_m']['median']} m, p90 {st['height_m']['p90']} m; extent {st['extent_m']} m")
    r = st.get("repetition")
    if r:
        print("typologies by footprint:", ", ".join(f"{k} {v:.0%}" for k, v in list(r["share"].items())[:8]),
              f"(+{max(0, len(r['share']) - 8)} more)")
        print("district diversity:", ", ".join(f"{k} {v}" for k, v in r["diversity"].items()),
              f"; columns on {r['column_share']:.0%}; longest identical run {r['longest_identical_run']}; "
              f"{r['thin_tiles']}/{r['urban_tiles']} urban tiles under the type minimum")
    for w in st["warnings"]:
        print("warning:", w)


def cmd_site_roll(a) -> None:
    from . import city_roll
    store = ProjectStore.open(a.project)
    keep = [k.strip() for k in (a.keep or "").split(",") if k.strip()]
    if a.step:
        r = city_roll.step(store, -1 if a.step == "previous" else 1)
        print(f"roll {r['pos'] + 1} of {r['count']}: seed {r['seed']}, {r['stats']['buildings']} buildings")
        return
    r = city_roll.roll(store, a.seed, keep, preset=a.preset, layout_seed=a.layout_seed, keep_shape=a.keep_shape)
    print(f"rolled a city: seed {r['seed']}{f' (keeping {', '.join(r['keep'])})' if r['keep'] else ''}, "
          f"{r['stats']['buildings']} buildings{f', {r['tries']} tries' if r['tries'] > 1 else ''}; "
          f"plan {city_roll.rolls_path(store).parent / 'city_plan.png'}")
    for w in r["stats"]["warnings"]:
        print("warning:", w)
    print(f"reproduce it: ap site roll {a.project} --seed {r['seed']}" + (f" --keep {','.join(r['keep'])}" if r["keep"] else ""))


def cmd_site_preset(a) -> None:
    from . import city_roll, layout_presets
    if a.list or not (a.name or a.layout_seed is not None):
        for p in layout_presets.summary():
            print(f"{p['name']:14s} {p['description']}")
        return
    store = ProjectStore.open(a.project)
    st = city_roll.set_preset(store, a.name, a.layout_seed)
    lay = city_roll._layout_or_starter(store)
    print(f"layout preset {lay.city.preset.name}, layout seed {lay.city.layout_seed}: {st['buildings']} buildings; "
          f"plan {city_roll.rolls_path(store).parent / 'city_plan.png'}")
    for w in st["warnings"]:
        print("warning:", w)


def cmd_site_catalog(a) -> None:
    from .stages import sw_site
    store = ProjectStore.open(a.project)
    if a.what == "import":
        if not a.file:
            raise SystemExit("ap site catalog import PROJECT FILE.yaml")
        L = sw_site.import_catalog(store, Path(a.file))
        print(f"{len(L.city.typologies)} typologies, {len(L.districts)} districts, {len(L.materials)} materials "
              f"in {sw_site.site_dir(store) / 'layout.json'}. Next: ap site plan {a.project}")
    else:
        text = sw_site.export_catalog(store)
        if a.file:
            Path(a.file).write_text(text)
            print(a.file)
        else:
            print(text)


def cmd_site_build(a) -> None:
    from .stages import sw_site
    store = ProjectStore.open(a.project)
    sw_site.build(store, force=a.force)
    _print_site(store)


def cmd_site_overlap(a) -> None:
    from .stages import sw_site
    rep = sw_site.plan_overlap(ProjectStore.open(a.project), a.shot)
    print("IoU with the frame's plan: " + ", ".join(f"{k} {v}" for k, v in rep["iou"].items()))
    print("area m2 (frame / greybox): " + ", ".join(f"{k} {rep['frame_m2'][k]} / {rep['greybox_m2'][k]}"
                                                    for k in rep["iou"]))


def cmd_site_extract(a) -> None:
    from .stages import sw_site
    store = ProjectStore.open(a.project)
    data = sw_site.extract(store)
    for f in data["fixed"]:
        print(f"fixed: {f}")
    _print_site(store)


def cmd_site_show(a) -> None:
    _print_site(ProjectStore.open(a.project), pieces=a.pieces)


def _print_site(store, pieces: bool = False) -> None:
    from .stages import sw_site
    s = sw_site.summary(store)
    state = "edited by hand" if s["edited"] else "as built"
    print(f"{sw_site.blend_path(store)} ({state}{', greybox.json STALE: run ap site extract' if s['stale'] else ''})")
    print(f"{s['slots']} slots: " + ", ".join(f"{n} {t}" for t, n in s["types"].items()))
    if pieces:
        for p, n in s["pieces"].items():
            print(f"  {n:5d}  {p}")
    print(f"shots: {', '.join(s['shots']) or 'none'}")
    if s["untagged"]:
        print(f"warning: {len(s['untagged'])} untagged meshes (give them ap_id/ap_type or parent them to a slot): "
              f"{', '.join(s['untagged'][:8])}{' ...' if len(s['untagged']) > 8 else ''}")


def cmd_site_vegetation(a) -> None:
    from . import vegetation
    store = ProjectStore.open(a.project)
    rec = vegetation.street_tree_slots(store)
    print(f"{vegetation.path(store)}: {rec['counts']['street']} street-tree slots, {rec['counts']['plaza']} plaza (settings: "
          f"layout city.street_trees; the city plan is unchanged)")
    for p in vegetation.preview(store, a.preview) if a.preview else []:
        print(p)


def cmd_site_preview(a) -> None:
    from .stages import sw_shots
    for p in sw_shots.preview_site(ProjectStore.open(a.project)):
        print(p)


def cmd_shots_add(a) -> None:
    from .schema import ShotSpec
    from .stages import sw_site
    store = ProjectStore.open(a.project)
    w, h = _size(a.res)
    shot = ShotSpec(id=a.id, tier=a.tier, pos=_vec(a.pos), look_at=_vec(a.look_at), lens_mm=a.lens,
                    resolution=(w, h), district=a.district, notes=a.notes or "")
    sw_site.add_shot(store, shot)
    print(f"shot {a.id} in {sw_site.blend_path(store)}. Next: ap shots render {a.project} {a.id}")


def cmd_shots_list(a) -> None:
    from .stages import sw_shots
    for r in sw_shots.status(ProjectStore.open(a.project)):
        state = "not rendered" if not r["rendered"] else ("STALE" if r["stale"] else f"rendered {r['rendered']}")
        print(f"{r['id']:20s} {r['tier']:6s} {r['lens_mm']:5.0f} mm  {r['resolution'][0]}x{r['resolution'][1]}  "
              f"{r['district'] or '-':12s} {state}")
        for w in r["warnings"]:
            print(f"    warning: {w}")


def cmd_shots_render(a) -> None:
    from .stages import sw_shots
    store = ProjectStore.open(a.project)
    for sid, r in sw_shots.render(store, a.shots or None).items():
        st = r["stats"]
        print(f"{sw_shots.shot_dir(store, sid)}: {st['visible_slots']} slots visible, "
              f"geometry {st['hit_frac']:.0%} of the frame, depth {st['z_range_m']} m")
        for w in r["warnings"]:
            print(f"    warning: {w}")


def cmd_moodboard_import(a) -> None:
    from .stages import s0_style
    store = ProjectStore.open(a.project)
    keys = s0_style.import_moodboard(store, a.group, [Path(p) for p in a.paths])
    print(f"{len(keys)} image(s) -> {store.root / s0_style.MOODBOARD}/")


def cmd_moodboard_show(a) -> None:
    from .stages import s0_style
    store = ProjectStore.open(a.project)
    board = s0_style.moodboard(store)
    for g, keys in board.items():
        print(f"{g}: {len(keys)} image(s)")
    if not board:
        print("no moodboard yet (ap moodboard import PROJECT GROUP FOLDER)")
    a_ = store.load().anchor
    print(f"style text: {a_.style_text if a_ and a_.style_text else '(none)'}")


def cmd_moodboard_style(a) -> None:
    from .stages import s0_style
    store = ProjectStore.open(a.project)
    d = s0_style.draft_style_text(store, a.group or None, max_images=a.max_images, seed=a.seed)
    print(d.style_text)
    print(f"palette: {d.palette}\navoid: {d.avoid}", file=sys.stderr)
    if a.save:
        s0_style.set_style_text(store, d.style_text)
        print("saved as the project's style text", file=sys.stderr)


def cmd_style_text(a) -> None:
    from .stages import s0_style
    store = ProjectStore.open(a.project)
    if a.text is None:
        anchor = store.load().anchor
        print(anchor.style_text if anchor else "")
    else:
        s0_style.set_style_text(store, a.text)
        print("style text saved")


CONTROL_FLAGS = {"depth_strength": "depth", "depth_end": "depth_end", "canny_strength": "canny",
                 "canny_end": "canny_end", "depth_image": "depth_image"}


def _frame_settings(a, store):
    """The project's frame settings with the command line's flags applied. For `generate`,
    a control flag applies to every shot (the raised / aerial overrides are dropped); for
    `settings --view V` the control flags edit that view's overrides."""
    from .schema import ViewControl
    from .stages import s0_frames
    fs = s0_frames.settings(store)
    ctrl = {k: getattr(a, f) for k, f in CONTROL_FLAGS.items() if getattr(a, f) is not None}
    upd = {k: v for k, v in {"model": a.model, "steps": a.steps, "min_ref_coverage": a.min_ref_coverage}.items()
           if v is not None}
    view = getattr(a, "view", None)
    if view:
        views = dict(fs.views)
        views[view] = (views.get(view) or ViewControl()).model_copy(update=ctrl)
        upd["views"] = views
    elif ctrl:
        upd |= ctrl
        if a.action == "generate":
            upd["views"] = {}
    if getattr(a, "reset_views", False):
        upd["views"] = type(fs)().views
    return fs.model_copy(update=upd) if upd else fs


def cmd_frames_generate(a) -> None:
    from .stages import s0_frames, sw_site
    store = ProjectStore.open(a.project)
    fs = _frame_settings(a, store)
    locked = s0_frames.locked(store)
    if a.shots:
        shots = a.shots
        held = [s for s in shots if s in locked and not a.force]
        if held:
            raise SystemExit(f"locked (unlock, or --force): {', '.join(held)}")
    else:
        shots = [s["id"] for s in sw_site.load_greybox(store)["shots"] if s["id"] not in locked]
        if locked:
            print(f"skipping locked: {', '.join(sorted(locked))}")
    for shot in shots:
        out = s0_frames.generate(store, shot, n=a.n, seed=a.seed, fs=fs, refs=a.ref, ref_strength=a.ref_strength,
                                material_refs=not a.no_material_refs, force=a.force,
                                out=a.out / shot if a.out else None)
        meta = json.loads((out / "meta.json").read_text())
        print(f"{out}: {len(meta['frames'])} frame(s), edge match "
              + ", ".join(f"{f['edge_match']:.2f}" for f in meta["frames"]))


def cmd_frames_settings(a) -> None:
    store = ProjectStore.open(a.project)
    fs = _frame_settings(a, store)
    if any(v is not None for v in (a.model, a.depth, a.depth_end, a.canny, a.canny_end, a.depth_image, a.steps,
                                   a.min_ref_coverage)) \
            or a.reset_views:
        p = store.load()
        p.frames = fs
        store.save(p)
        print("saved:", end=" ")
    print(fs.model_dump_json())


def cmd_frames_role(a) -> None:
    from . import review
    store = ProjectStore.open(a.project)
    role = None if a.role == "none" else a.role.replace("-", "_")
    key = review.set_role(store, a.frame, role, a.note)
    print(f"{key}: {role or 'no role'}" + (f" ({a.note})" if a.note else ""))


def cmd_frames_lock(a) -> None:
    from .stages import s0_frames
    store = ProjectStore.open(a.project)
    if not (a.unlock or a.frame or a.greybox):
        raise SystemExit("give a frame, --greybox or --unlock")
    key = s0_frames.lock(store, a.shot, None if a.unlock else s0_frames.LOCK_GREYBOX if a.greybox else a.frame)
    print(f"{a.shot}: locked on {key}" if key else f"{a.shot}: unlocked")


def cmd_frames_refs(a) -> None:
    from . import review
    from .stages import sw_site
    store = ProjectStore.open(a.project)
    spec = sw_site.shot_spec(store, a.shot)
    upd = {}
    if a.clear:
        upd["refs"] = []
    if a.set:
        upd["refs"] = [review.rel(store, r) for r in a.set]
    if a.strength is not None:
        upd["ref_strength"] = a.strength
    if upd:
        spec = sw_site.update_shot(store, a.shot, **upd)
    print(f"{a.shot}: {len(spec.refs)} design reference(s) at {spec.ref_strength}")
    for r in spec.refs:
        print(f"    {r}")


def cmd_library_plan(a) -> None:
    from .stages import sw_library
    store = ProjectStore.open(a.project)
    data = sw_library.plan(store, min_share=a.min_share)
    print(sw_library.summary(data))
    print(f"{store.root / 'site/library.json'}, {store.root / 'site/library.html'}")


def cmd_library_check(a) -> None:
    from .stages import sw_library
    store = ProjectStore.open(a.project)
    data = sw_library.check(store, min_share=a.min_share,
                            progress=lambda i, n: print(f"  SAM {i}/{n}", end="\r", flush=True))
    print(sw_library.summary(data))


def cmd_library_decide(a) -> None:
    from .stages import sw_library
    store = ProjectStore.open(a.project)
    pieces = json.loads(a.pieces) if a.pieces else None
    if a.description is not None:
        sw_library.describe(store, a.entry, a.description or None)
    d = sw_library.decide(store, a.entry, None if a.decision == "none" else a.decision, a.note, a.frame, pieces)
    print(f"{a.entry}: {d.get(a.entry, {}).get('decision', 'undecided')}"
          + (f" (reference {d[a.entry]['frame']})" if d.get(a.entry, {}).get("frame") else ""))


def cmd_library_review(a) -> None:
    from . import library_review as lr
    store = ProjectStore.open(a.project)
    if a.what == "apply":
        if a.done:
            rnd = lr.done(store, a.done)
            print(f"marked Ready for review: {', '.join(a.done)}; round {rnd} is open")
            return
        print(lr.task_list(store))
    else:
        rv = lr.reviews(store)
        print(f"round {lr.current_round(store)}")
        for asset, r in sorted(rv.items()):
            print(f"  {asset:24s} {lr.STATUSES.get(r.get('status'), '-'):18s} {r.get('note', '').replace(chr(10), ' / ')[:90]}")


def cmd_library_versions(a) -> None:
    from . import versions
    store = ProjectStore.open(a.project)
    if a.what == "migrate":
        r = versions.migrate(store)
        print(f"{r['versions']} version(s) made for {r['assets']} asset(s)")
        for asset, n in r["detail"].items():
            print(f"  {asset}: {n}")
        return
    s = versions.storage(store)
    print(f"keep the last {s['keep']} per asset (plus the Active and Approved); versions {s['versions_bytes'] / 1e6:.1f} MB, builds {s['current_bytes'] / 1e6:.1f} MB")
    for r in s["assets"]:
        print(f"  {r['asset']:28s} {r['versions']:3d} version(s) {r['versions_bytes'] / 1e6:8.1f} MB   build {r['current_bytes'] / 1e6:8.1f} MB")


def cmd_library_route(a) -> None:
    from .stages import sw_library
    store = ProjectStore.open(a.project)
    d = sw_library.set_route(store, a.entry, None if a.route == "auto" else a.route)
    e = next(x for x in (lambda p: p["entries"] + p["unseen"])(sw_library.load(store)) if x["id"] == a.entry)
    print(f"{a.entry}: {sw_library.route(e, d.get(a.entry)) or 'not built (foliage)'}"
          f"{' (yours)' if d.get(a.entry, {}).get('route') else ' (default)'}")


def cmd_library_form(a) -> None:
    from . import library_entries
    store = ProjectStore.open(a.project)
    try:
        info = library_entries.set_form(store, a.entry, None if a.form == "auto" else a.form, a.size)
    except (KeyError, ValueError) as e:
        raise SystemExit(str(e).strip('"'))
    print(f"{a.entry}: form {info['form'] or '-'} ({info['source'] or 'none'}), size "
          f"{' x '.join(f'{x:g}' for x in info['size_m']) if info['size_m'] else '-'} ({info['size_source'] or 'none'})")


def cmd_library_stand_in(a) -> None:
    from .stages import sw_library
    store = ProjectStore.open(a.project)
    if not (a.clear or a.target):
        raise SystemExit("ap library stand-in PROJECT ENTRY TARGET (or --clear)")
    try:
        s = sw_library.set_stand_in(store, a.entry, None if a.clear else a.target)
    except (KeyError, ValueError) as e:
        raise SystemExit(str(e))
    print("\n".join(f"{k} -> {v}" for k, v in sorted(s.items())) or "no stand-ins")


def cmd_library_variants(a) -> None:
    from . import variants
    store = ProjectStore.open(a.project)
    try:
        if a.remove:
            variants.remove(store, a.base)
            print(f"{a.base}: variant set removed")
            return
        if a.member or a.areas is not None or not variants.sets(store).get(a.base):
            old = {m["entry"]: m["weight"] for m in (variants.summary(store, a.base)["members"]
                                                       if variants.sets(store).get(a.base) else [])}
            for m in a.member or []:
                e, _, w = m.partition("=")
                old[e] = float(w or 1.0)
            for e in a.drop or []:
                old.pop(e, None)
            variants.save(store, a.base, old, a.areas)
        s = variants.summary(store, a.base)
    except (KeyError, ValueError) as e:
        raise SystemExit(str(e).strip('"'))
    print(f"{a.base}: {s['slots']} {s['prop']} place(s); areas {', '.join(s['areas']) or 'all'}")
    for m in s["members"]:
        print(f"  {m['entry']:28s} weight {m['weight']:g} ({100 * m['share']:.0f}%)  {m['slots']} place(s)")
    if a.area:
        for r in variants.assign(store, a.base, a.area):
            print(f"  {r['key']:28s} {r['entry']}")


def cmd_library_brief(a) -> None:
    from . import library_entries
    store = ProjectStore.open(a.project)
    try:
        b = library_entries.brief_from_sheet(store, a.entry, a.sheet, a.view, a.form, a.dims, a.params)
    except ValueError as e:
        raise SystemExit(str(e))
    m = b["measured"]
    print(f"{a.entry}: {b['form']} {b['dims']}, view {b['view']} (frontality {m.get('frontality')}); measured storeys "
          f"{m.get('storeys')}, bay {m.get('bay_m')} m")


COMFY_DOWN = ("ComfyUI is not answering at {url}. Start it with `ap comfy start` (or the app's Status box), "
              "then run the same command again: finished items are skipped.")


def _comfy_up() -> bool:
    try:
        connect().health()
        return True
    except Exception:
        return False


def _selection(a, store) -> list[dict] | None:
    """The area's (or district's) asset rows (areas.status), or None without --area / --district."""
    if not (getattr(a, "area", None) or getattr(a, "district", None)):
        return None
    from . import areas
    try:
        return areas.status(store, a.area, a.district)
    except (KeyError, FileNotFoundError, ValueError) as e:
        raise SystemExit(str(e).strip('"'))


def _batch(items, fn, what: str) -> None:
    """Run fn per item; a failure is reported and the batch goes on. Ends with a summary and
    the exit code 1 when anything failed (rerun the same command: finished items are skipped)."""
    failed = []
    for x in items:
        try:
            fn(x)
        except KeyboardInterrupt:
            raise
        except Exception as e:                   # noqa: BLE001 - every failure is reported, the rest go on
            print(f"{x}: FAILED: {type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}")
            failed.append(x)
    print(f"{what}: {len(items) - len(failed)} done, {len(failed)} failed" + (f" ({', '.join(failed)})" if failed else ""))
    if failed:
        raise SystemExit(1)


def cmd_library_build(a) -> None:
    from .kit_proc import build_any, spec_path
    store = ProjectStore.open(a.project)
    rows = _selection(a, store)
    if rows is None:
        todo = list(a.entries)
        if not todo:
            raise SystemExit("name the assets, or give --area NAME / --district ID")
    else:
        todo = [r["asset"] for r in rows if r["route"] == "procedural" and r["brief"] and r["status"] != "approved"
                and (a.force or not r["fresh"])]
        skipped = [r["asset"] for r in rows if r["status"] == "approved"]
        print(f"{len(todo)} to build" + (f"; approved, skipped: {', '.join(skipped)}" if skipped else "")
              + ("" if a.force else "; builds of an unchanged brief are skipped (--force rebuilds them)"))
    missing = [x for x in todo if not spec_path(store, x).is_file()]
    if missing:
        raise SystemExit(f"no brief for {', '.join(missing)}: make one with ap library brief {a.project} ASSET SHEET front")

    def one(x):
        if a.renders:                                # build + the review round's renders, as Save & Rebuild
            from . import library_review as lr
            print(f"{x}: {lr.rebuild(store, x, log=lambda s: None)['images']}")
        else:
            build_any(store, x, log=lambda s: print(f"{x}: {s}"), surface=a.surface)
    _batch(todo, one, "build")


def cmd_library_trellis(a) -> None:
    """TRELLIS for the area's props (trellis route): each asset's chosen view, skipping approved
    assets and those that have a result already (--more makes another)."""
    from . import review
    from .stages import s5_3d, sw_library
    store = ProjectStore.open(a.project)
    rows = _selection(a, store)
    if rows is None:
        raise SystemExit("give --area NAME or --district ID")
    rows = [r for r in rows if r["route"] == "trellis" and r["status"] != "approved" and (a.more or not r["trellis"])]
    nochoice = [r["asset"] for r in rows if not review.chosen(store, sw_library.PLAN_NAME, r["asset"])]
    if nochoice:
        print(f"no view chosen (Review tab: click a view of each), skipped: {', '.join(nochoice)}")
    todo = [r["asset"] for r in rows if r["asset"] not in nochoice]
    print(f"{len(todo)} to make (GPU 0; the VLM is stopped while TRELLIS runs)")
    _batch(todo, lambda x: print(f"{x}: {s5_3d.run(store, sw_library.PLAN_NAME, x, mode=a.mode)}"), "TRELLIS")


def cmd_files(a) -> None:
    """The project's shared file store (filestore.py): migrate, stats, clean."""
    from . import filestore
    store = ProjectStore.open(a.project)
    if a.action == "migrate":
        filestore.migrate(store)
    elif a.action == "clean":
        r = filestore.clean(store, dry_run=a.dry_run)
        print(f"{'would remove' if a.dry_run else 'removed'} {r['removed']} unreferenced file(s), {r['bytes'] / 1e6:.1f} MB")
    else:
        s = filestore.stats(store)
        print(f"{s['objects']} stored files, {s['bytes'] / 1e6:.1f} MB on disk, {s['references']} references "
              f"({s['referenced_bytes'] / 1e6:.1f} MB as copies): {s['saved'] / 1e6:.1f} MB saved; "
              f"{s['unreferenced']} unreferenced ({s['unreferenced_bytes'] / 1e6:.1f} MB)")


def cmd_area(a) -> None:
    from . import areas
    store = ProjectStore.open(a.project)
    if a.action == "define":
        if not a.name:
            raise SystemExit("ap area define PROJECT NAME --shots ... [--districts ...]")
        try:
            print(areas.define(store, a.name, a.shots, a.districts))
        except ValueError as e:
            raise SystemExit(str(e))
        return
    if a.action == "list":
        for k, v in areas.load(store).items():
            print(f"{k}: shots {', '.join(v['shots']) or '-'}; districts {', '.join(v['districts']) or '-'}")
        return
    if a.action == "checklist":
        if not a.name:
            raise SystemExit("ap area checklist PROJECT NAME")
        print(areas.checklist(store, a.name))
        return
    try:
        rows = areas.status(store, a.name, a.district)
    except (KeyError, FileNotFoundError, ValueError) as e:
        raise SystemExit(str(e).strip('"'))
    for r in rows:
        print(f"{r['asset']:28s} {r['tier'] or '-'} {r['route']:10s} {100 * r['share']:5.1f}%  "
              f"{(r['status'] or 'not reviewed'):14s} next: {r['next']}"
              + (f"  (variant of {r['variant_of']})" if r.get("variant_of") else ""))
    gaps = areas.form_gaps(store, rows)
    if gaps:
        print(f"\nform gaps ({len(gaps)}): the first fix is the suggested one")
        for g in gaps:
            print(f"  {g['asset']:28s} no {', no '.join(g['missing'])}")
            for f in g["fixes"]:
                cmd = {"form": f"ap library form {a.project} {g['asset']} {f.get('form')}"
                               + (" --size " + " ".join(f"{x:g}" for x in f["size_m"]) if f.get("size_m") else ""),
                       "route": f"ap library route {a.project} {g['entry']} {f.get('route')}"}.get(f["kind"], "Manage entry… in the app")
                print(f"      {f['label']}: {cmd}")


def cmd_library_instances(a) -> None:
    from . import library_instances
    from .stages import sw_site
    store = ProjectStore.open(a.project)
    near = None
    if a.near:
        s = next((x for x in sw_site.load_layout(store).shots if x.id == a.near), None)
        if s is None:
            raise SystemExit(f"no shot {a.near} (ap shots list {a.project})")
        near = tuple(s.effective().pos[:2])
    rows = _selection(a, store)
    entries = list(a.entries) if rows is None else sorted({r["asset"] for r in rows if r["route"] == "procedural" and r["brief"]
                                                          and r["kind"] == "building"})
    if not entries:
        raise SystemExit("name the entries, or give --area NAME / --district ID")
    have = {p.stem for p in (store.root / "library/kits").glob("*.json")}
    typed = [e for e in entries if e in have]

    def one(e):
        idx = library_instances.build_entries(store, [e], near, a.radius, skip_fresh=not a.force)[e]
        print(f"{e}: {len(idx)} instances -> 3d/library/{e}/instances/")
    _batch(typed, one, "instances")


def cmd_library_silhouette(a) -> None:
    from . import look
    store = ProjectStore.open(a.project)
    for e in a.entries:
        look.silhouette_of(store, e, a.shot)


def cmd_library_pending(a) -> None:
    from . import look
    rows = look.pending_slots(ProjectStore.open(a.project), a.shot, draft=not a.no_draft)
    for r in rows:
        print(f"{r['frame_share'] * 100:6.2f}%  {r['slot']}: {'; '.join(r['state'])}")


def cmd_library_repair_plan(a) -> None:
    from . import library_entries
    store = ProjectStore.open(a.project)
    r = library_entries.repair_plan(store, dry_run=a.dry_run)
    verb = "would add" if a.dry_run else "added"
    for t in r["added"]:
        flags = ", ".join(x for x in ("built" if t["built"] else "", "approved" if t["approved"] else "") if x)
        print(f"{verb} {t['asset']}: {t['decision']}, {t['sheets']} sheet(s)" + (f", {flags}" if flags else ""))
    for t in r["skipped"]:
        print(f"skipped {t['asset']}: {t['why']}")
    print(f"{verb} {len(r['added'])} asset(s)" + (f", skipped {len(r['skipped'])}" if r["skipped"] else "")
          + f"; {r['included']} asset(s) are in plans/library.json" + (" now" if not a.dry_run else ""))


def cmd_library_status(a) -> None:
    from . import areas
    from .stages import sw_library
    store = ProjectStore.open(a.project)
    st = areas.library_status(store)
    if not st["steps"]:
        raise SystemExit("no library yet (ap library plan)")
    names = sw_library.asset_names(store)
    nm = lambda ids: ", ".join(f"{names[i]} ({i})" if names.get(i, i) != i else i for i in ids) or "-"   # noqa: E731
    for i, s in enumerate(areas.STEPS, 1):
        ids = sorted(x for x, v in st["steps"].items() if v == s)
        print(f"{i} {areas.STEP_LABELS[s]}: {len(ids)}\n   {nm(ids)}")
    c = st["counts"]
    print(f"Needs views (a starred sheet nothing was cut from): {c['needs_views']}\n   {nm(st['needs_views'])}")
    lay = st["not_in_layout"]
    print(f"Not in the city layout: {len(lay)} ({sum(1 for v in lay.values() if not v['appears'])} never appear, left out of Build and Approve)")
    for a, v in sorted(lay.items()):
        print(f"   {nm([a])}: {v['reason']} ({'appears in the city' if v['appears'] else 'never appears'})")
    print(f"{c['with_views']} of {c['assets']} placed assets have views, {c['with_model']} have a 3D model")


def cmd_library_sheets(a) -> None:
    from . import areas
    from .stages import s2_refs, sw_library
    store = ProjectStore.open(a.project)
    rows = _selection(a, store)
    need_assets = None
    if rows is not None:                             # the area's assets that need a sheet (areas.sheet_skip)
        need = areas.sheet_rows(store, rows)
        need_assets = {r["asset"] for r in need}
        a.only = sorted({r["entry"] for r in need})
        undecided = sorted({r["entry"] for r in rows if not r["decision"]})
        if undecided:
            print(f"not decided yet (Library › Entries), skipped: {', '.join(undecided)}")
        if not a.only:
            raise SystemExit("nothing to do in this area")
    if not a.plan_only and not _comfy_up():
        raise SystemExit(COMFY_DOWN.format(url=config.backends()["comfyui"]["url"]))
    plan = sw_library.asset_plan(store, only=a.only)        # adds these; every asset already included stays included
    inc = [x for x in plan.assets if x.include]
    print(f"plans/library.json: {len(plan.assets)} decided assets, {len(inc)} included")
    only_assets = None
    if a.only:                                       # --only / an area: sheets for these entries' assets only
        from . import library_entries
        only_assets = {x for e in a.only for x in library_entries.asset_ids(store, e)}
        inc = [x for x in inc if x.id in only_assets]
    for x in inc:
        print(f"  {x.id}: {x.dimensions.width:g} x {x.dimensions.depth:g} x {x.dimensions.height:g} m, "
              f"refs {x.refs or '-'} at {x.ref_strength:g}")
    if a.plan_only:
        return
    if a.guidance and not a.unit:
        raise SystemExit("--guidance steers one asset's reroll: give it with --unit ASSET")
    todo = [u for u in s2_refs.units(store, sw_library.PLAN_NAME) if (not a.unit or u.key in a.unit)
            and u.key in {x.id for x in inc} and (a.more or a.unit or not s2_refs.sheets(store, u))
            and (need_assets is None or a.more or a.unit or u.key in need_assets)
            and (only_assets is None or a.unit or u.key in only_assets)]
    for u in todo:
        try:
            out = s2_refs.generate(store, u, n=a.n, view=a.view, guidance=a.guidance)
        except Exception as e:                       # noqa: BLE001 - ComfyUI gone mid-batch: say how to resume
            if not _comfy_up():
                raise SystemExit(f"{u.key}: {type(e).__name__}. " + COMFY_DOWN.format(url=config.backends()["comfyui"]["url"]))
            raise
        print(f"{u.key}: {', '.join(p.name for p in out)}")
    print(f"sheets: {len(todo)} unit(s) made; units with sheets were skipped (--more adds sheets to them)")


def cmd_library_show(a) -> None:
    from .stages import sw_library
    data = sw_library.load(ProjectStore.open(a.project))
    if data is None:
        raise SystemExit("no library plan yet: ap library plan PROJECT")
    print(sw_library.summary(data))


def cmd_library_smooth(a) -> None:
    from . import smooth
    store = ProjectStore.open(a.project)
    if a.list:
        todo = smooth.candidates(store)
        print("\n".join(todo) if todo else "no unsmoothed TRELLIS models in use")
        return
    if a.all == bool(a.assets):
        raise SystemExit("name assets, or --all for every asset whose Active version is an unsmoothed TRELLIS model")
    if a.version is not None:
        if len(a.assets) != 1:
            raise SystemExit("--version goes with one asset")
        r = smooth.smooth_version(store, a.assets[0], a.version, angle_deg=a.angle)
        print(f"{r['asset']}: v{r['from']} -> v{r['n']} ({r['welded']} vertices welded, {r['seconds']} s)")
        return
    rep = smooth.smooth_all(store, None if a.all else a.assets)
    print(f"{len(rep['done'])} smoothed, {len(rep['failed'])} failed")


def cmd_library_remove_ground(a) -> None:
    from . import smooth
    store = ProjectStore.open(a.project)
    if a.list:
        todo = smooth.candidates(store, op="ground")
        print("\n".join(todo) if todo else "every TRELLIS model in use has had its ground looked for")
        return
    if a.all == bool(a.assets):
        raise SystemExit("name assets, or --all for every asset whose Active version is a TRELLIS model not checked yet")
    if a.version is not None:
        if len(a.assets) != 1:
            raise SystemExit("--version goes with one asset")
        r = smooth.fix_version(store, a.assets[0], a.version, smooth=a.smooth, ground=True)
        print(f"{r['asset']} v{r['from']}: {smooth.ground_line(r['ground'])}" + (f" -> v{r['n']}" if r["n"] else ""))
        return
    rep = smooth.fix_all(store, None if a.all else a.assets, smooth=a.smooth, ground=True)
    print(f"{len(rep['done'])} checked, {sum(1 for r in rep['done'] if r['n'])} new versions, {len(rep['failed'])} failed")


def cmd_frames_show(a) -> None:
    from .stages import s0_frames, sw_site
    store = ProjectStore.open(a.project)
    stars = set(s0_frames.approved(store))
    mood = s0_frames.mood_shots(store)
    locked = s0_frames.locked(store)
    for shot in a.shots or [s["id"] for s in sw_site.load_greybox(store)["shots"]]:
        bs = s0_frames.batches(store, shot)
        n = sum(len(b["frames"]) for b in bs)
        print(f"{shot}: {n} frame(s), {sum(1 for b in bs for f in b['frames'] if f['key'] in stars)} approved"
              + ("  (mood only: no assets)" if shot in mood else "")
              + (f"  LOCKED on {locked[shot]}" if shot in locked else ""))
        if a.prompt:
            try:
                print(f"    prompt: {s0_frames.prompt_for(store, shot)}")
            except FileNotFoundError as e:
                print(f"    ({e})")
        for b in bs:
            for f in b["frames"]:
                print(f"    {'*' if f['key'] in stars else ' '} {f['key']}  match {f['edge_match']:.2f}")


def cmd_export(a) -> None:
    from .stages import s7_export
    store = ProjectStore.open(a.project)
    c = s7_export.export(store, assets=a.assets, use_active=a.use_active)
    print(f"{s7_export.export_dir(store) / 'manifest.json'}: {c['assets']} stand-in assets, {c['slots']} slots, "
          f"{c['instances']} instances, {c['shots']} shots")


def _print_ue_report(rep) -> None:
    c = rep["checks"]
    print(f"{'OK' if rep['ok'] else 'PROBLEMS'}: {len(rep['assets']['imported'])} assets imported, "
          f"{len(rep['assets']['skipped'])} unchanged; {len(rep['actors']['created'])} actors created, "
          f"{len(rep['actors']['kept_transform'])} kept their UE transform, {len(rep['actors']['reset'])} reset; "
          f"{c['instances_total']} instances, {c['cameras']} cameras; sequence {rep['sequence']['path']} "
          f"({rep['sequence']['sections']} shots) in {rep['seconds']} s")
    lib = rep.get("library") or {}
    if lib:
        print(f"library builds: {lib['placed']} placed, {lib['removed']} removed"
              + (f", {len(lib['unattached'])} without a slot actor (placed in the world)" if lib.get("unattached") else ""))
    secs = rep.get("import_seconds") or {}
    if secs:
        slow = sorted(secs.items(), key=lambda kv: -kv[1])[:3]
        print(f"import time: {sum(secs.values()):.0f} s for {len(secs)} assets (slowest: "
              + ", ".join(f"{k} {v:.1f} s" for k, v in slow) + "); per asset in export/ue/import_seconds.json")
    if rep.get("moved_in_ue"):
        print(f"moved in UE (kept): {', '.join(rep['moved_in_ue'])}")
    for k in ("nanite_off", "missing_mi", "instance_mismatch"):
        for x in c[k]:
            print(f"problem: {k}: {x}")
    from .ue import orphan_breakdown
    orphans, pruned = rep.get("orphans", []), rep.get("pruned", [])
    breakdown = ", ".join(f"{t} {n}" for t, n in orphan_breakdown(orphans).items())
    if pruned:
        print(f"pruned {len(pruned)} pipeline actors not in the manifest ({breakdown})")
    elif orphans:
        print(f"{len(orphans)} pipeline actors not in the manifest ({breakdown}); "
              + (f"NOT pruned: needs --expect {rep['prune_refused']} (or confirmation)"
                 if rep.get("prune_refused") is not None else "--prune --expect N deletes them"))
    warnings = rep["sample_errors"] + rep["warnings"] + rep.get("log_errors", [])
    for x in warnings:
        print(f"warning: {x}")
    if warnings:
        print(f"{len(warnings)} warning(s)")


def _stale(store) -> dict:
    from . import layout_history
    return layout_history.check(store)


def _print_stale(cls: dict) -> None:
    """Slots away from the current greybox: stale (an earlier greybox put them there) or
    moved by hand (no committed greybox did)."""
    print(f"{len(cls['current'])} slots where the current greybox puts them; {len(cls['stale'])} stale "
          f"(at an earlier committed greybox's place); {len(cls['hand'])} moved by hand")
    for sid in cls["hand"]:
        print(f"moved by hand (kept): {sid}")


def _confirm_prune(n: int, breakdown: dict[str, int]) -> bool:
    """Ask before a prune past the automatic limit: the user types the count."""
    print(f"prune would delete {n} pipeline actors not in the manifest: "
          + ", ".join(f"{t} {k}" for t, k in breakdown.items()))
    return input(f"type {n} to delete them (anything else keeps them): ").strip() == str(n)


def cmd_ue(a) -> None:
    from . import ue
    store = ProjectStore.open(a.project)
    if a.action == "init":
        print(ue.init(store))
    elif a.action == "stale":
        _print_stale(_stale(store))
    elif a.action == "import":
        if a.only_stale and not a.reset_layout:
            raise SystemExit("--only-stale goes with --reset-layout")
        only = None
        if a.only_stale:
            cls = _stale(store)
            _print_stale(cls)
            only = sorted(cls["stale"])
        _print_ue_report(ue.import_(store, reset_layout=a.reset_layout, prune=a.prune, expect=a.expect,
                                    confirm=_confirm_prune if sys.stdin.isatty() else None, reset_only=only,
                                    vegetation=a.vegetation))
    elif a.action == "render":
        rep = ue.render_shots(store, a.shots or None)
        for s in rep["shots"]:
            print(s["file"])
        for s in rep["failed"]:
            print(f"failed: {s}")
        if rep.get("error"):
            print(f"error: {rep['error']}")
    elif a.action == "look":
        from . import look
        if len(a.shots) != 1:
            raise ValueError("ap ue look PROJECT NAME (a preset in site/look_tests/NAME.json)")
        rep = look.run(store, a.shots[0])
        r = rep["record"]
        print(f"{r['render']}: {r['placed']} library builds, GPU {r['render_gpu']}, exposure {r['exposure']}, ok={r['ok']}")
    elif a.action == "pull-layout":
        rep = ue.pull_layout(store)
        print(f"{len(rep['actors'])} actors -> {store.root / 'site/ue_layout.json'}")
    elif a.action == "build":
        from . import ue_build
        if a.reset_layout and a.only_stale:
            raise SystemExit("--reset-layout or --only-stale, not both (build keeps hand moves by default: --only-stale)")
        from . import dressing
        off = {k: False for k in ("ground", "rocks", "vegetation", "megascans")} | {"water": "off"} if a.no_dressing else {}
        opts = dressing.merged(store, **(off | {k: getattr(a, v) for k, v in DRESS_FLAGS.items() if getattr(a, v) is not None}))
        unplaced = {"avoid_locked": not a.unplaced_in_locked_shots, "seed": a.unplaced_seed} if a.use_unplaced else None
        r = ue_build.build(store, use_active=a.use_active, reset_layout=a.reset_layout, render=a.render, dressing=opts,
                           unplaced=unplaced)
        print("\n".join(ue_build.report_lines(r)))
    elif a.action == "scan-assets":
        from . import dressing
        if a.folder:
            dressing.save(store, folders=_unique(dressing._unreal(store).dressing_folders + a.folder))
        s = dressing.scan(store)
        print(f"scanned {len(s['folders_scanned'])} folder(s) under {s['content']}")
        lib = dict(dressing._unreal(store).dressing_library)
        for role in dressing.ROLES:
            n, new = len(s["candidates"][role]), s["proposal"].get(role)
            cur = ", ".join(dressing.assets_of(lib.get(role))) or "-"
            shown = ", ".join(dressing.assets_of(new))
            print(f"  {role:13} {cur:60} " + (f"proposed: {shown} ({n} found)" if new and new != lib.get(role) else f"{n} found"))
        if a.apply:
            dressing.save(store, library=lib | s["proposal"])
            print("saved the proposed mapping (roles found); your other roles kept")
        p = dressing.plugin_status()
        print(p["help"])
    elif a.action == "backup":
        rep = ue.backup(store, keep=a.keep, force=a.force)
        print(f"{rep['snapshot']}: {rep['files']} files, {rep['new_mb']} MB new (the rest hard-linked), "
              f"{rep['seconds']} s; keeping {len(rep['kept'])}"
              + (f", removed {', '.join(rep['removed'])}" if rep["removed"] else ""))


DRESS_FLAGS = {"ground": "ground", "rocks": "rocks", "rock_density": "rock_density", "vegetation": "dress_vegetation",
               "vegetation_density": "vegetation_density", "water": "water", "megascans": "megascans", "seed": "dress_seed"}


def _unique(xs: list) -> list:
    return list(dict.fromkeys(xs))


def cmd_terrain_roll(a) -> None:
    """ap terrain presets | roll PROJECT --preset NAME [--seed N] [--describe TEXT] [--replace-heightmap]"""
    import numpy as np
    from . import city_roll, terrain_gen
    if a.action == "presets":
        for p in terrain_gen.summary():
            print(f"{p['name']:14s} {p['description']}")
        return
    if not a.project:
        raise SystemExit("ap terrain roll PROJECT --preset NAME | --describe TEXT")
    store = ProjectStore.open(a.project)
    seed = a.seed if a.seed is not None else int(np.random.default_rng().integers(1, 2**31 - 1))
    if getattr(a, "describe", None):
        from . import terrain_describe
        pick = terrain_describe.describe(a.describe, prefer=a.preset)
        print(f"described as {pick['preset']} ({pick['source']}): {pick['summary']}")
        settings = pick["settings"].model_copy(update={"seed": seed})
    elif a.preset:
        settings = terrain_gen.get(a.preset, seed)
    else:
        raise SystemExit("give --preset NAME (ap terrain presets) or --describe TEXT")
    st = city_roll.set_terrain(store, settings, replace_heightmap=a.replace_heightmap)
    print(f"terrain {settings.name}, terrain seed {seed}: {st['buildings']} buildings; "
          f"plan {city_roll.rolls_path(store).parent / 'city_plan.png'}")
    for w in st["warnings"]:
        print("warning:", w)
    print(f"reproduce it: ap terrain roll {a.project} --preset {settings.name} --seed {seed}"
          if settings.name in terrain_gen.PRESETS and not getattr(a, "describe", None) else "")


def cmd_terrain(a) -> None:
    if a.action in ("roll", "presets"):
        return cmd_terrain_roll(a)
    from . import terrain_build as tb
    from . import terrain_plan as tp
    store = ProjectStore.open(a.project)
    if a.action == "analyse":
        r = tp.analyse(store)
        for sh in r["shots"]:
            if sh["lock"] == "frame":
                print(f"{sh['shot']}: ridge bearings {sh.get('bearing_span')}, elevation {sh.get('elevation_span')}")
    elif a.action == "build":
        r = tb.build(store)
        for x in r["ranges"]:
            print(f"{x['name']}: height factor {x['height_factor']}, width {x['width_factor']}, foothills {x['foothill_factor']}")
        print(f"cells in a locked frame's sky after erosion: {r['cells_over_sky_after_erosion']}; max z {r['max_z']} m")
    elif a.action == "outer":
        print(tb.rebuild_outer(store))
        print(tb.outer_glb(store))
    elif a.action == "apply":
        print(tb.apply(store))
    elif a.action == "landscape":
        print(tb.landscape_tiles(store))
        print(tb.outer_glb(store))
    elif a.action == "ue":
        print(tb.ue_assets(store))


def cmd_ui(a) -> None:
    import subprocess
    import uvicorn
    from .ui.app import STATIC, build_id, create_app
    code = Path(__file__).resolve().parents[1]
    try:
        rev = subprocess.run(["git", "-C", str(code), "describe", "--always", "--dirty"], capture_output=True,
                             text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        rev = ""
    print(f"Terraformer UI: http://127.0.0.1:{a.port}")
    print(f"  code: {code}" + (f" (git {rev})" if rev else " (not a git checkout: an installed copy, not the repo)"))
    print(f"  static: {STATIC} · build {build_id()} (files are read on each request; reload the page after an update)")
    uvicorn.run(create_app(), host="127.0.0.1", port=a.port, log_level="warning")


def cmd_launcher(a) -> None:
    from . import launcher
    if a.action == "install":
        for k, v in launcher.install(port=a.port).items():
            print(f"{k}: {v}")
    elif a.action == "remove":
        gone = launcher.remove()
        print("\n".join(f"removed {g}" for g in gone) or "nothing installed")
    else:
        st = launcher.status()
        for k in ("desktop", "icon", "script"):
            print(f"{k}: {st[k]['path']} ({'present' if st[k]['exists'] else 'missing'})")


def cmd_vlm(a) -> None:
    from . import vlm
    if a.action == "status":
        print(json.dumps(vlm.status(), indent=2))
    elif a.action == "down":
        print(vlm.down(gpu=a.gpu if a.gpu_given else None, force=a.force))
    else:
        st = vlm.up(a.model, gpu=a.gpu, idle_unload=a.idle_unload)
        print(f"vlm {st['model']} up on GPU {st['gpu']} at {st['url']} (pid {st['pid']}); "
              f"log: {config.ap_root() / 'logs' / ('vlm-' + st['model'] + '.log')}")


def cmd_set(a) -> None:
    from .llm.registry import PROVIDERS
    from .paid import paid_allowed
    store = ProjectStore.open(a.project)
    p = store.load()
    if a.llm:
        if a.llm not in PROVIDERS:
            raise ValueError(f"--llm must be one of {', '.join(PROVIDERS)}")
        p.llm = a.llm
    for item in a.backend or []:
        stage, _, name = item.partition("=")
        if name not in ("comfyui", "gemini"):
            raise ValueError(f"--backend {item!r}: expected STAGE=comfyui|gemini")
        p.backends[stage] = name
    store.save(p)
    print(f"{p.slug}: llm={p.llm}, backends={p.backends}")
    paid = [x for x in [p.llm] + list(p.backends.values()) if x in ("gemini", "claude")]
    if paid and not paid_allowed():
        print(f"note: {', '.join(sorted(set(paid)))} are paid APIs and stay blocked unless "
              f"AP_ALLOW_PAID_APIS=1 is set for the run.")


def main(argv: list[str] | None = None) -> None:
    from . import config
    config.load_secrets()
    ap = argparse.ArgumentParser(prog="ap", description="Terraformer (see USAGE.md)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    cf = sub.add_parser("config", help="the user config (~/.config/terraformer/config.toml): path, show, get, set, unset, migrate")
    csub = cf.add_subparsers(dest="config_cmd", required=True)
    csub.add_parser("path", help="where the config, the secrets file and storage are")
    csub.add_parser("show", help="the effective settings (API key values are never shown)")
    p = csub.add_parser("get", help="one effective value, e.g. comfyui.url")
    p.add_argument("key")
    p = csub.add_parser("set", help="write one value into the user config, e.g. tools.blender /usr/bin/blender")
    p.add_argument("key")
    p.add_argument("value")
    p = csub.add_parser("unset", help="remove one value from the user config (the default applies again)")
    p.add_argument("key")
    p = csub.add_parser("migrate", help="write the user config for an older install from its AP_ROOT (and its backends.local.toml)")
    p.add_argument("root", help="the old AP_ROOT")
    p.add_argument("--ue-editor-cmd", help="the UnrealEditor-Cmd the old install used")
    p.add_argument("--ue-projects", help="the old [unreal] projects_dir")
    p.add_argument("--ue-backups", help="the old [unreal] backup_dir")
    cf.set_defaults(fn=cmd_config)

    mo = sub.add_parser("models", help="the models the pipeline needs: list, download (resume, checksums, --json progress), verify")
    msub = mo.add_subparsers(dest="models_cmd", required=True)
    p = msub.add_parser("list", help="every model with installed or missing, size and what uses it")
    p.add_argument("--all", action="store_true", help="include experimental models")
    p.add_argument("--json", action="store_true")
    for name, hlp in (("download", "download missing models (resumes partial files, verifies checksums)"),
                      ("verify", "hash installed files against the manifest")):
        p = msub.add_parser(name, help=hlp)
        p.add_argument("names", nargs="*", help="model ids from `ap models list`")
        p.add_argument("--required", action="store_true", help="every required model")
        p.add_argument("--all", action="store_true", help="every model but the experimental ones")
        p.add_argument("--feature", choices=["comfy", "vlm", "segdepth", "trellis", "dressing"], help="only this feature's models (dressing: CC0 world-dressing assets)")
        p.add_argument("--json", action="store_true", help="progress as JSON lines (event, model, file, done, total, ...)")
        if name == "download":
            p.add_argument("--dry-run", action="store_true", help="show the plan and the disk check; download nothing")
    mo.set_defaults(fn=cmd_models)

    p = sub.add_parser("doctor", help="check everything (storage, ComfyUI, models, GPUs, Blender, Unreal) and say how to fix what's missing")
    p.add_argument("--json", action="store_true", help="the report as JSON (what the app's Status box shows)")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("setup", help="the setup wizard in your browser (components, storage, ComfyUI, Blender, Unreal, GPUs); "
                                     "--terminal, --yes or --check ask and test in this terminal instead")
    p.add_argument("--terminal", action="store_true", help="ask in this terminal instead of opening the wizard")
    p.add_argument("--no-open", action="store_true", help="wizard: print its address instead of opening the browser")
    p.add_argument("--yes", action="store_true", help="take the current or detected values without asking")
    p.add_argument("--check", action="store_true", help="only test the current settings; write nothing")
    p.add_argument("--set", action="append", metavar="ITEM=VALUE",
                   help="set one item, e.g. --set blender=/usr/bin/blender (repeatable; items: storage, comfy_backend, "
                        "comfy_url, sdk_url, comfy_launch, comfy_cwd, blender, unreal, unreal_projects, gpu_vision, gpu_comfy)")
    p.set_defaults(fn=cmd_setup)

    p = sub.add_parser("new", help="create a project under $AP_ROOT/projects/")
    p.add_argument("slug", nargs="?", help="folder name (my-project), or a project name to make it from")
    p.add_argument("--name", help="project name; without a folder name the folder is made from it")
    p.add_argument("--brief")
    p.add_argument("--start", choices=["city", "blender", "assets"],
                   help="starting point: procedural city (default), own Blender layout, concept images to assets")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("comfy-check", help="check the ComfyUI server is reachable")
    p.set_defaults(fn=cmd_comfy_check)

    p = sub.add_parser("comfy", help="start / stop / restart / status of the local ComfyUI (config [comfyui.service])")
    p.add_argument("action", choices=["start", "stop", "restart", "status"])
    p.add_argument("-v", "--verbose", action="store_true", help="start: print every log line, not only errors")
    p.set_defaults(fn=cmd_comfy)
    p = sub.add_parser("preflight", help="check ComfyUI (+ its models), local models, Blender and Unreal for the demo path")
    p.add_argument("--ue", action="store_true", help="Unreal is required (the last demo steps)")
    p.set_defaults(fn=cmd_preflight)

    p = sub.add_parser("gen", help="generate images through the image adapter")
    p.add_argument("prompt")
    p.add_argument("-n", type=int, default=1)
    p.add_argument("--seed", type=int)
    p.add_argument("--size", default="1024x1024", help="WxH (default 1024x1024)")
    p.add_argument("--steps", type=int, help="sampler steps (backend default if unset)")
    p.add_argument("--project", help="project slug: use its backends, log to its runs.jsonl")
    p.add_argument("--stage", default="style", help="which stage's backend to use (default style)")
    p.add_argument("--backend", help="override: comfyui | gemini")
    p.add_argument("--anchor", action="store_true", help="apply the project's style anchor")
    p.add_argument("--out", type=Path, help="output dir (default: project or $AP_ROOT/outputs/gen/<time>)")
    p.set_defaults(fn=cmd_gen)

    st = sub.add_parser("style", help="stage 0: explore scenes, derive the style anchor")
    ssub = st.add_subparsers(dest="style_cmd", required=True)
    p = ssub.add_parser("explore", help="generate scene concepts from the brief")
    p.add_argument("project")
    p.add_argument("--brief", help="defaults to the project's brief")
    p.add_argument("-n", type=int, default=8)
    p.add_argument("--seed", type=int)
    p.set_defaults(fn=cmd_style_explore)
    p = ssub.add_parser("star", help="star (or --unstar) images by path")
    p.add_argument("project")
    p.add_argument("paths", nargs="+")
    p.add_argument("--unstar", action="store_true")
    p.set_defaults(fn=cmd_style_star)
    p = ssub.add_parser("derive", help="cut objects from a starred scene and redraw them on white")
    p.add_argument("project")
    p.add_argument("scene", help="scene image path (absolute or project-relative)")
    p.add_argument("--nouns", required=True, help="comma-separated objects, e.g. 'boat,house'")
    p.add_argument("--per-noun", type=int, default=2)
    p.add_argument("--style-text", help="defaults to the current anchor's style text")
    p.add_argument("--no-redraw", action="store_true", help="only cut out, don't regenerate")
    p.set_defaults(fn=cmd_style_derive)
    p = ssub.add_parser("anchor", help="save starred derived objects as the style anchor")
    p.add_argument("project")
    p.add_argument("--strength", type=float, default=0.06)
    p.add_argument("--style-text", help="keeps the current style text if omitted")
    p.set_defaults(fn=cmd_style_anchor)
    p = ssub.add_parser("text", help="print the project's style text, or set it")
    p.add_argument("project")
    p.add_argument("text", nargs="?")
    p.set_defaults(fn=cmd_style_text)
    p = ssub.add_parser("show", help="list batches, stars and the anchor")
    p.add_argument("project")
    p.set_defaults(fn=cmd_style_show)

    pl = sub.add_parser("plan", help="stage 1: scene -> asset plan via the vision LLM")
    psub = pl.add_subparsers(dest="plan_cmd", required=True)
    p = psub.add_parser("import", help="copy outside scene images into the project's scenes/")
    p.add_argument("project")
    p.add_argument("images", nargs="+", type=Path)
    p.set_defaults(fn=cmd_plan_import)
    p = psub.add_parser("analyze", help="draft plans (default: every starred/imported scene)")
    p.add_argument("project")
    p.add_argument("scenes", nargs="*", help="scene paths (absolute or project-relative)")
    p.add_argument("--force", action="store_true", help="replace plans edited in the UI")
    p.add_argument("--no-refine", action="store_true",
                   help="keep the LLM's boxes (default: SAM 3.1 boxes if ComfyUI is up)")
    p.set_defaults(fn=cmd_plan_analyze)
    p = psub.add_parser("refine", help="redo asset boxes with SAM 3.1 (needs ComfyUI)")
    p.add_argument("project")
    p.add_argument("name", nargs="?", help="one plan (default: all)")
    p.set_defaults(fn=cmd_plan_refine)
    p = psub.add_parser("show", help="print plans")
    p.add_argument("project")
    p.add_argument("name", nargs="?", help="one plan, e.g. batch_001__scene_002")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_plan_show)

    rf = sub.add_parser("refs", help="stage 2: reference sheets for the plans' assets")
    rsub = rf.add_subparsers(dest="refs_cmd", required=True)
    p = rsub.add_parser("generate", help="sheets for units without any (or --unit to add more)")
    p.add_argument("project")
    p.add_argument("--plan", help="one plan only")
    p.add_argument("--unit", action="append", help="asset id or kit-<name> (repeatable)")
    p.add_argument("-n", type=int, default=2, help="sheets per unit (default 2)")
    p.add_argument("--seed", type=int)
    p.set_defaults(fn=cmd_refs_generate)
    p = rsub.add_parser("show", help="list units, sheet counts and stars")
    p.add_argument("project")
    p.add_argument("--plan")
    p.set_defaults(fn=cmd_refs_show)

    p = sub.add_parser("views", help="stage 3: cut views out of starred reference sheets")
    vsub = p.add_subparsers(dest="views_cmd", required=True)
    p = vsub.add_parser("cut", help="cut starred sheets without views (or the given sheets)")
    p.add_argument("project")
    p.add_argument("sheets", nargs="*", help="sheet paths (project-relative), re-cut even if done")
    p.set_defaults(fn=cmd_views_cut)

    rv = sub.add_parser("review", help="stage 4: choose views, tag game / cine / hero")
    rvsub = rv.add_subparsers(dest="review_cmd", required=True)
    p = rvsub.add_parser("show", help="assets with their views, choice, tag and 3D results")
    p.add_argument("project")
    p.set_defaults(fn=cmd_review_show)
    p = rvsub.add_parser("choose", help="the view an asset goes to 3D with")
    p.add_argument("project")
    p.add_argument("plan")
    p.add_argument("asset")
    p.add_argument("view", nargs="?", help="view path (omit to clear)")
    p.set_defaults(fn=cmd_review_choose)
    p = rvsub.add_parser("tag", help="usage: game | cine | hero")
    p.add_argument("project")
    p.add_argument("plan")
    p.add_argument("asset")
    p.add_argument("usage", choices=["game", "cine", "hero"])
    p.set_defaults(fn=cmd_review_tag)

    p = sub.add_parser("3d", help="stage 5: TRELLIS.2 on an asset's chosen view (GPU 0)")
    p.add_argument("project")
    p.add_argument("plan")
    p.add_argument("asset")
    p.add_argument("--view", help="override the chosen view")
    p.add_argument("--mode", default="1024_cascade", help="1024_cascade (default) | 512 | 1024")
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(fn=cmd_3d)

    hr = sub.add_parser("hero", help="EXPERIMENTAL hero tier: Wan 2.2 turntable orbit of the chosen view")
    hsub = hr.add_subparsers(dest="hero_cmd", required=True)
    p = hsub.add_parser("orbit", help="orbit video frames + drift / closure analysis (GPU 1, ~3 min)")
    p.add_argument("project")
    p.add_argument("plan")
    p.add_argument("asset")
    p.add_argument("--seed", type=int)
    p.set_defaults(fn=cmd_hero_orbit)
    p = hsub.add_parser("angles", help="frame angles by matching the TRELLIS mesh's renders (unreliable)")
    p.add_argument("project")
    p.add_argument("plan")
    p.add_argument("asset")
    p.add_argument("orbit", help="orbit dir name, e.g. orbit_s2")
    p.set_defaults(fn=cmd_hero_angles)
    p = hsub.add_parser("mesh", help="EXPERIMENTAL: Hunyuan3D-2mv shape from orbit frames you pick (GPU 1, ~30 s; httpx backend only)")
    p.add_argument("project")
    p.add_argument("plan")
    p.add_argument("asset")
    p.add_argument("orbit", help="orbit dir name, e.g. orbit_s2")
    p.add_argument("--front", type=int, default=0, help="frame number (default 0)")
    p.add_argument("--left", type=int, help="frame showing the asset's own left side")
    p.add_argument("--back", type=int)
    p.add_argument("--right", type=int)
    p.add_argument("--seed", type=int, default=1)
    p.set_defaults(fn=cmd_hero_mesh)

    p = sub.add_parser("cleanup", help="stage 6: scale to plan size, base pivot, game retopo (Blender, CPU)")
    p.add_argument("project")
    p.add_argument("plan")
    p.add_argument("asset")
    p.add_argument("--glb", help="project-relative GLB (default: the newest 3D result)")
    p.add_argument("--fit", default="height", choices=["height", "geomean"])
    p.add_argument("--budget", type=int, help="game triangle budget (default by category)")
    p.set_defaults(fn=cmd_cleanup)

    si = sub.add_parser("site", help="world mode: site layout -> Blender greybox (source of truth)")
    sisub = si.add_subparsers(dest="action", required=True)
    p = sisub.add_parser("init", help="copy a layout (default: a small neutral starter) into the project")
    p.add_argument("project")
    p.add_argument("--layout", help="layout.json to start from")
    p.add_argument("--force", action="store_true", help="replace an existing site/layout.json")
    p.add_argument("--city", action="store_true", help="start from the neutral starter city (city mode)")
    p.set_defaults(fn=cmd_site_init)
    p = sisub.add_parser("plan", help="city mode: plan image + area report, no Blender (fast)")
    p.add_argument("project")
    p.add_argument("--shots", action="store_true", help="also replace the layout's shots with the auto-placed ones")
    p.add_argument("--shot", action="append", help="re-place only this auto shot (repeatable); the others keep their cameras")
    p.set_defaults(fn=cmd_site_plan)
    p = sisub.add_parser("roll", help="city mode: roll a random city (rules + seed) and plan it; replaces the city rules")
    p.add_argument("project")
    p.add_argument("--seed", type=int, help="this seed (reproduces a roll); default a random one")
    p.add_argument("--keep", help="rule groups to leave as they are, comma-separated: "
                                  "size, civic, density, streets, green, waterfront")
    p.add_argument("--step", choices=["previous", "next"], help="go back / forward through the last 10 rolls instead")
    p.add_argument("--preset", help="the layout preset (ap site preset --list), or random; default the layout's own")
    p.add_argument("--layout-seed", type=int, help="the preset's own seed (outline, organic avenues); default the layout's own")
    p.add_argument("--keep-shape", action="store_true",
                   help="keep the current outline and districts (wall, towns, civic sites): new streets only")
    p.set_defaults(fn=cmd_site_roll)
    p = sisub.add_parser("preset", help="city mode: set the layout preset (the city's overall shape) and re-plan")
    p.add_argument("project", nargs="?")
    p.add_argument("name", nargs="?", help="the preset name; omit to list them")
    p.add_argument("--layout-seed", type=int, help="the preset's own seed (outline, organic avenues)")
    p.add_argument("--list", action="store_true", help="list the presets")
    p.set_defaults(fn=cmd_site_preset)
    p = sisub.add_parser("catalog", help="city mode: import / export the typology catalog (YAML)")
    p.add_argument("what", choices=["import", "export"])
    p.add_argument("project")
    p.add_argument("file", nargs="?", help="YAML to import (export: write here instead of stdout)")
    p.set_defaults(fn=cmd_site_catalog)
    p = sisub.add_parser("build", help="layout.json -> terrain + greybox.blend + greybox.json (Blender, CPU)")
    p.add_argument("project")
    p.add_argument("--force", action="store_true", help="replace a hand-edited greybox.blend")
    p.set_defaults(fn=cmd_site_build)
    p = sisub.add_parser("overlap", help="ground classes of the greybox vs a locked frame's plan (site/reconcile/SHOT)")
    p.add_argument("project")
    p.add_argument("shot")
    p.set_defaults(fn=cmd_site_overlap)
    p = sisub.add_parser("extract", help="re-read greybox.blend after editing it in Blender")
    p.add_argument("project")
    p.set_defaults(fn=cmd_site_extract)
    p = sisub.add_parser("show", help="slots, shots and edit state")
    p.add_argument("project")
    p.add_argument("--pieces", action="store_true", help="also list kit pieces / massing with counts")
    p.set_defaults(fn=cmd_site_show)
    p = sisub.add_parser("vegetation", help="street-tree slots on streets and plazas (data for Unreal PCG; "
                                            "site/vegetation.json), with proxy-tree previews from shot cameras")
    p.add_argument("project")
    p.add_argument("--preview", nargs="+", metavar="SHOT", help="render these shots' greybox previews with proxy trees "
                                                                "(site/vegetation/<shot>/preview.png)")
    p.set_defaults(fn=cmd_site_vegetation)
    p = sisub.add_parser("preview", help="four aerial preview renders of the greybox")
    p.add_argument("project")
    p.set_defaults(fn=cmd_site_preview)

    sh = sub.add_parser("shots", help="world mode: shot cameras and their depth/canny/id passes")
    shsub = sh.add_subparsers(dest="action", required=True)
    p = shsub.add_parser("add", help="add or replace a shot camera (written to the .blend and the layout)")
    p.add_argument("project")
    p.add_argument("id")
    p.add_argument("--pos", required=True, help="x,y,z in metres")
    p.add_argument("--look-at", required=True, help="x,y,z in metres")
    p.add_argument("--lens", type=float, default=35.0, help="focal length in mm (36 mm sensor)")
    p.add_argument("--tier", choices=["wide", "medium", "tight"], default="medium")
    p.add_argument("--res", default="1344x768")
    p.add_argument("--district")
    p.add_argument("--notes")
    p.set_defaults(fn=cmd_shots_add)
    p = shsub.add_parser("list", help="shots with render state and warnings")
    p.add_argument("project")
    p.set_defaults(fn=cmd_shots_list)
    p = shsub.add_parser("render", help="render depth / canny / id / normal / preview passes (Blender, CPU)")
    p.add_argument("project")
    p.add_argument("shots", nargs="*", help="default: every shot")
    p.set_defaults(fn=cmd_shots_render)

    mb = sub.add_parser("moodboard", help="moodboard images for the style anchor (e.g. a PureRef export)")
    mbsub = mb.add_subparsers(dest="action", required=True)
    p = mbsub.add_parser("import", help="copy images or folders of images into style/moodboard/GROUP/")
    p.add_argument("project")
    p.add_argument("group")
    p.add_argument("paths", nargs="+")
    p.set_defaults(fn=cmd_moodboard_import)
    p = mbsub.add_parser("show", help="groups, image counts and the style text")
    p.add_argument("project")
    p.set_defaults(fn=cmd_moodboard_show)
    p = mbsub.add_parser("style", help="draft a style text from the moodboard (vision LLM, GPU 0)")
    p.add_argument("project")
    p.add_argument("--group", action="append", help="only these groups (repeatable)")
    p.add_argument("--max-images", type=int, default=9)
    p.add_argument("--seed", type=int, default=0, help="which images are sampled")
    p.add_argument("--save", action="store_true", help="save it as the project's style text")
    p.set_defaults(fn=cmd_moodboard_style)

    fr = sub.add_parser("frames", help="world mode stage 0: concept frames per shot, guided by the greybox")
    frsub = fr.add_subparsers(dest="action", required=True)
    for name, fn, helptext in (("generate", cmd_frames_generate, "concept frames per shot (ComfyUI, GPU 1, ~40 s each)"),
                               ("settings", cmd_frames_settings, "show the project's frame settings, or save new ones")):
        p = frsub.add_parser(name, help=helptext)
        p.add_argument("project")
        if name == "generate":
            p.add_argument("shots", nargs="*", help="default: every shot")
            p.add_argument("-n", type=int, default=4)
            p.add_argument("--seed", type=int)
            p.add_argument("--ref", action="append", help="approved frame (project-relative) to add as a Redux reference")
            p.add_argument("--ref-strength", type=float, default=0.08)
            p.add_argument("--no-material-refs", action="store_true",
                           help="materials by wording only (skip their masked reference images)")
            p.add_argument("--force", action="store_true", help="also generate locked shots named explicitly")
            p.add_argument("--out", type=Path, help="write each shot's batch to OUT/<shot> instead of the "
                                                    "project's frames/ (e.g. a demo run on a locked shot)")
        p.add_argument("--model", choices=["union", "depth_lora"])
        p.add_argument("--depth", type=float, help="depth control strength")
        p.add_argument("--depth-end", type=float, help="depth control stops at this fraction of the steps")
        p.add_argument("--canny", type=float, help="canny control strength (union; 0 = off)")
        p.add_argument("--canny-end", type=float)
        p.add_argument("--depth-image", choices=["plain", "relief"],
                       help="relief: depth with the local relief boosted (the default for raised and aerial shots)")
        p.add_argument("--steps", type=int)
        p.add_argument("--min-ref-coverage", type=float,
                       help="skip masked material / landmark references covering less of the frame (default 0.05)")
        if name == "settings":
            p.add_argument("--view", choices=["raised", "aerial"],
                           help="the control flags set this view's overrides instead of the level settings")
            p.add_argument("--reset-views", action="store_true", help="raised / aerial overrides back to the defaults")
        p.set_defaults(fn=fn)
    p = frsub.add_parser("role", help="mark a frame as a design reference (never an asset source), a source, or rejected")
    p.add_argument("project")
    p.add_argument("frame", help="project-relative frame path")
    p.add_argument("role", choices=["design-ref", "source", "rejected", "none"])
    p.add_argument("--note", help="what it's for (empty string clears)")
    p.set_defaults(fn=cmd_frames_role)
    p = frsub.add_parser("lock", help="finish a shot on one frame: never regenerated or re-placed")
    p.add_argument("project")
    p.add_argument("shot")
    p.add_argument("frame", nargs="?", help="project-relative frame of that shot")
    p.add_argument("--greybox", action="store_true",
                   help="lock the camera and greybox alone, no frame (a shot rendered in Unreal instead)")
    p.add_argument("--unlock", action="store_true")
    p.set_defaults(fn=cmd_frames_lock)
    p = frsub.add_parser("refs", help="show or set a shot's design references (frames added to the Redux anchor)")
    p.add_argument("project")
    p.add_argument("shot")
    p.add_argument("--set", nargs="+", metavar="FRAME", help="replace them with these project-relative frames")
    p.add_argument("--clear", action="store_true")
    p.add_argument("--strength", type=float, help="their total extra Redux strength")
    p.set_defaults(fn=cmd_frames_refs)
    p = frsub.add_parser("show", help="frames per shot with approval and edge match")
    p.add_argument("project")
    p.add_argument("shots", nargs="*")
    p.add_argument("--prompt", action="store_true", help="also print each shot's prompt")
    p.set_defaults(fn=cmd_frames_show)

    p = sub.add_parser("export", help="world mode phase 4: greybox -> export/ (GLBs + UE manifest)")
    p.add_argument("project")
    p.add_argument("--assets", action="store_true",
                   help="library builds stand in the city instead of their greybox (Approved ones; see --use-active)")
    p.add_argument("--use-active", action="store_true",
                   help="with --assets: every built asset's Active version, approved or not (previews)")
    p.set_defaults(fn=cmd_export)

    lib = sub.add_parser("library", help="world mode phase 3: the asset library from the locked shot frames")
    libsub = lib.add_subparsers(dest="action", required=True)
    p = libsub.add_parser("plan", help="entries from the locked frames' object-id passes (no generation)")
    p.add_argument("project")
    p.add_argument("--min-share", type=float, default=0.005,
                   help="share of a source frame an entry needs to get a crop (default 0.005)")
    p.set_defaults(fn=cmd_library_plan)
    p = libsub.add_parser("check", help="flag crops mostly trees, sky or something else (SAM 3.1 on ComfyUI)")
    p.add_argument("project")
    p.add_argument("--min-share", type=float, default=0.005)
    p.set_defaults(fn=cmd_library_check)
    p = libsub.add_parser("decide", help="the review decision for one entry")
    p.add_argument("project")
    p.add_argument("entry")
    p.add_argument("decision", choices=["source", "description", "reference", "kit", "exclude", "none"])
    p.add_argument("--frame", help="reference / kit: the project image used as look reference")
    p.add_argument("--pieces", help='kit: JSON {"bay": {"description": "...", "size": [w, d, h]}, ...}')
    p.add_argument("--description", help="your description for the entry ('' restores the auto one)")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_library_decide)
    p = libsub.add_parser("brief", help="procedural route: a building brief from one view of a sheet (measured)")
    p.add_argument("project")
    p.add_argument("entry")
    p.add_argument("sheet", help="project path of the reference sheet")
    p.add_argument("view", help="which view: 0 left, 1 middle, 2 right, all (the whole sheet), or front "
                                "(the most frontal view of the sheet)")
    p.add_argument("--form", choices=["hall_vault", "cube", "basilica", "podium_tower", "perimeter", "row"],
                   help="generator (default: from the entry's catalog form)")
    p.add_argument("--dims", type=float, nargs=3, help="w d h metres (default: the entry's size)")
    p.add_argument("--params", help="JSON generator parameters (storeys, bay_m, ...)")
    p.set_defaults(fn=cmd_library_brief)
    p = libsub.add_parser("build", help="procedural route: build entries' kit pieces / buildings in Blender")
    p.add_argument("project")
    p.add_argument("entries", nargs="*", help="assets to build (or --area / --district)")
    p.add_argument("--area", help="every procedural asset of this area with a brief (ap area define)")
    p.add_argument("--district", help="every procedural asset with instances in this district")
    p.add_argument("--force", action="store_true", help="--area / --district: rebuild unchanged briefs too")
    p.add_argument("--renders", action="store_true", help="also the review renders (Library › Asset review), as Save & Rebuild")
    p.add_argument("--surface", choices=["patch", "library"],
                   help="buildings' storey-band surfaces: a tile cut from the sheet, or the material library "
                        "(type per band read off the sheet, scanned PBR set tinted to its colour); default: "
                        "the brief's `surface`, else building_proc.DEFAULT_SURFACE")
    p.set_defaults(fn=cmd_library_build)
    p = libsub.add_parser("review", help="build reviews: the task list of assets marked Needs changes / Needs new "
                                         "feature (apply), or every asset's status (show)")
    p.add_argument("what", choices=["apply", "show"])
    p.add_argument("project")
    p.add_argument("--done", nargs="+", metavar="ASSET", help="apply: mark these handled (Ready for review) and open "
                                                              "the next round")
    p.set_defaults(fn=cmd_library_review)
    p = libsub.add_parser("versions", help="asset version bank: migrate (every existing build becomes version 1 of its "
                                           "asset; safe to repeat) or show (sizes per asset)")
    p.add_argument("what", choices=["migrate", "show"])
    p.add_argument("project")
    p.set_defaults(fn=cmd_library_versions)
    p = libsub.add_parser("route", help="how an entry's 3D is made: procedural (architecture) or trellis")
    p.add_argument("project")
    p.add_argument("entry")
    p.add_argument("route", choices=["procedural", "trellis", "auto"])
    p.set_defaults(fn=cmd_library_route)
    p = libsub.add_parser("form", help="an entry's building form and size for its brief (Manage entry… › Form, Size); "
                                       "forms: " + ", ".join(__import__("asset_pipeline.building_gen", fromlist=["FORM_CHOICE"]).FORM_CHOICE))
    p.add_argument("project")
    p.add_argument("entry", help="an entry, or a kit module (entry-piece)")
    p.add_argument("form", help="a form choice, or auto (back to the catalog's)")
    p.add_argument("--size", type=float, nargs=3, metavar=("W", "D", "H"), help="width, depth, height (m)")
    p.set_defaults(fn=cmd_library_form)
    p = libsub.add_parser("stand-in", help="a building entry with no model of its own (an excluded one stays excluded) takes "
                                           "another built entry's model at its planned buildings")
    p.add_argument("project")
    p.add_argument("entry")
    p.add_argument("target", nargs="?", help="the built entry whose model stands for it")
    p.add_argument("--clear", action="store_true", help="back to the entry's own")
    p.set_defaults(fn=cmd_library_stand_in)
    p = libsub.add_parser("variants", help="a prop type's variant set: its places pick from several entries by weight, "
                                           "reproducibly per place (show; --member ENTRY=WEIGHT adds or reweights)")
    p.add_argument("project")
    p.add_argument("base", help="the prop type's entry (e.g. canopy-table); a set is made on first use")
    p.add_argument("--member", nargs="+", metavar="ENTRY=WEIGHT")
    p.add_argument("--drop", nargs="+", metavar="ENTRY", help="leave these entries out of the set")
    p.add_argument("--areas", nargs="*", help="only these areas' places pick variants (none given: every area)")
    p.add_argument("--area", help="list each place of this area with the entry it picks")
    p.add_argument("--remove", action="store_true", help="remove the set (every place takes the base entry)")
    p.set_defaults(fn=cmd_library_variants)
    p = libsub.add_parser("instances", help="procedural route: build entries at each of their buildings' own footprint")
    p.add_argument("project")
    p.add_argument("entries", nargs="*", help="entries (or --area / --district)")
    p.add_argument("--area")
    p.add_argument("--district")
    p.add_argument("--force", action="store_true", help="rebuild instances whose brief and size haven't changed too")
    p.add_argument("--near", metavar="SHOT", help="only the buildings within --radius of this shot's camera")
    p.add_argument("--radius", type=float, default=600.0)
    p.set_defaults(fn=cmd_library_instances)
    p = libsub.add_parser("silhouette", help="builds vs their building's mask in a shot's locked frame (IoU, aspect)")
    p.add_argument("project")
    p.add_argument("entries", nargs="+")
    p.add_argument("--shot", required=True)
    p.set_defaults(fn=cmd_library_silhouette)
    p = libsub.add_parser("pending", help="slots a shot shows without an approved build, by frame share, with "
                                          "frame crops and briefs drafted by the vision model")
    p.add_argument("project")
    p.add_argument("--shot", required=True)
    p.add_argument("--no-draft", action="store_true", help="skip the vision model's drafts")
    p.set_defaults(fn=cmd_library_pending)
    p = libsub.add_parser("repair-plan", help="put every decided, non-excluded asset that has a sheet back into "
                                              "plans/library.json (assets an older plan rewrite dropped)")
    p.add_argument("project")
    p.add_argument("--dry-run", action="store_true", help="list what it would add, change nothing")
    p.set_defaults(fn=cmd_library_repair_plan)
    p = libsub.add_parser("status", help="where every asset waits (Decide, Sheets, Star, Build, Approve), with the "
                                         "asset names: the numbers the UI's step strip and Generate 3D Model & Review show")
    p.add_argument("project")
    p.set_defaults(fn=cmd_library_status)
    p = libsub.add_parser("sheets", help="decided entries -> plans/library.json (added to it, never dropped from it), "
                                         "then stage 2 sheets")
    p.add_argument("project")
    p.add_argument("--area", help="only this area's decided, unapproved entries (ap area define)")
    p.add_argument("--district", help="only this district's decided, unapproved entries")
    p.add_argument("--only", nargs="+", help="just these entries (a pilot)")
    p.add_argument("-n", type=int, default=2, help="sheets per asset")
    p.add_argument("--more", action="store_true", help="also for assets that have sheets already")
    p.add_argument("--plan-only", action="store_true", help="write the plan, generate nothing")
    p.add_argument("--unit", nargs="+", help="only these sheet units (asset ids, e.g. a kit module), more sheets "
                                             "even if they have some; the plan keeps every entry included")
    p.add_argument("--view", choices=["turnaround", "three-quarter"], default="turnaround",
                   help="object sheet layout: front/side/back, or three-quarter views (deep assets)")
    p.add_argument("--guidance", help="with --unit: words added to the asset's prompt for this batch only, saved "
                                      "with its sheets (e.g. \"slatted timber counters, no stone\")")
    p.set_defaults(fn=cmd_library_sheets)
    p = libsub.add_parser("trellis", help="TRELLIS route: 3D for the area's props from their chosen views (GPU 0)")
    p.add_argument("project")
    p.add_argument("--area")
    p.add_argument("--district")
    p.add_argument("--mode", default="1024_cascade")
    p.add_argument("--more", action="store_true", help="also props that have a result (another try)")
    p.set_defaults(fn=cmd_library_trellis)
    p = libsub.add_parser("show", help="the library plan, by district")
    p.add_argument("project")
    p.set_defaults(fn=cmd_library_show)
    p = libsub.add_parser("smooth", help="TRELLIS models: weld and shade smooth by angle, as a new version "
                                         "(the Active one when its source was); seconds, no regeneration")
    p.add_argument("project")
    p.add_argument("assets", nargs="*")
    p.add_argument("--all", action="store_true", help="every asset whose Active version is an unsmoothed TRELLIS model")
    p.add_argument("--list", action="store_true", help="only list those assets")
    p.add_argument("--version", type=int, help="smooth this version instead of the Active one (one asset)")
    p.add_argument("--angle", type=float, help="degrees (default [trellis] smooth_angle, 30)")
    p.set_defaults(fn=cmd_library_smooth)
    p = libsub.add_parser("remove-ground", help="TRELLIS models: cut off a thin ground slab (a shadow or floor "
                                                "TRELLIS built under the model) as a new version; plinths and steps stay")
    p.add_argument("project")
    p.add_argument("assets", nargs="*")
    p.add_argument("--all", action="store_true", help="every asset whose Active version is a TRELLIS model not checked yet")
    p.add_argument("--list", action="store_true", help="only list those assets")
    p.add_argument("--version", type=int, help="this version instead of the Active one (one asset)")
    p.add_argument("--smooth", action="store_true", help="smooth the shading in the same pass (where not done yet)")
    p.set_defaults(fn=cmd_library_remove_ground)
    p = sub.add_parser("ue", help="world mode phase 5: Unreal Engine 5.8 project, import, renders")
    p.add_argument("action", choices=["build", "init", "import", "stale", "render", "look", "pull-layout", "backup",
                                      "scan-assets"],
                   help="build: the whole city headless (backup, export with library builds, terrain, dressing, import, "
                        "light, optional --render; keeps hand moves unless --reset-layout); scan-assets: find Megascans "
                        "in the UE project and propose the dressing library (--apply saves it)")
    p.add_argument("project")
    p.add_argument("shots", nargs="*", help="render: only these shots; look: the look preset's name "
                                            "(site/look_tests/NAME.json: a shot rebuilt in a level of its own)")
    p.add_argument("--reset-layout", action="store_true",
                   help="import: move every actor back to the manifest (discards layout edits made in UE)")
    p.add_argument("--prune", action="store_true",
                   help="import: delete pipeline actors not in the manifest (more than 10 needs --expect "
                        "or confirmation at a terminal; headless runs never go past that check)")
    p.add_argument("--only-stale", action="store_true",
                   help="import --reset-layout: move back only stale slots (placed by an earlier committed "
                        "greybox); slots moved by hand in Unreal are listed and kept")
    p.add_argument("--expect", type=int, metavar="N",
                   help="import --prune: delete only if exactly N actors are missing from the manifest")
    p.add_argument("--vegetation", action="store_true",
                   help="import: also place the vegetation points (export/vegetation.json) as tagged TargetPoints for a "
                        "PCG graph (untested in Unreal as of 2026-10-16)")
    p.add_argument("--use-active", action="store_true",
                   help="build: place every built asset's Active version, approved or not (previews)")
    p.add_argument("--render", action="store_true",
                   help="build: render the shots afterwards (GPU 1, only while ComfyUI is down)")
    p.add_argument("--use-unplaced", action="store_true",
                   help="build: built assets with no slot in the city plan also stand in the city, in a share of "
                        "the places that fit them (the plan, greybox, shots and frames stay; off restores the plan's assets)")
    p.add_argument("--unplaced-in-locked-shots", action="store_true",
                   help="build --use-unplaced: may also take places a locked shot's frame shows (default: avoided)")
    p.add_argument("--unplaced-seed", type=int, default=0, metavar="N",
                   help="build --use-unplaced: which places it takes (the same seed picks the same places)")
    dg = p.add_argument_group("world dressing (build; unset = the project's last-used choice, which these update)")
    bo = argparse.BooleanOptionalAction
    dg.add_argument("--ground", action=bo, default=None, help="ground textures by slope and height (Light)")
    dg.add_argument("--rocks", action=bo, default=None, help="rock clusters on open ground (Light-Medium)")
    dg.add_argument("--rock-density", type=float, metavar="X", help="rocks: x the default count (0.1-4)")
    dg.add_argument("--dress-vegetation", action=bo, default=None,
                    help="tree meshes for placeholder trees, shrubs and grass in green zones (Medium-Heavy)")
    dg.add_argument("--vegetation-density", type=float, metavar="X", help="shrubs, grass, olives: x the default (0.1-4)")
    dg.add_argument("--water", choices=["off", "simple", "ocean"], help="simple sea material (Light) or the Water "
                    "plugin's ocean (Heavy, experimental; falls back to simple)")
    dg.add_argument("--megascans", action=bo, default=None, help="use the dressing library's Megascans where mapped")
    dg.add_argument("--dress-seed", type=int, metavar="N", help="placement seed")
    dg.add_argument("--no-dressing", action="store_true", help="every dressing option off (removes what was placed)")
    p.add_argument("--folder", action="append", metavar="/Game/...", help="scan-assets: also scan this folder (remembered)")
    p.add_argument("--apply", action="store_true", help="scan-assets: save the proposed mapping")
    p.add_argument("--keep", type=int, help="backup: snapshots to keep (default: config [unreal] backup_keep, 10)")
    p.add_argument("--force", action="store_true", help="backup: even while an editor has the project open")
    p.set_defaults(fn=cmd_ue)

    p = sub.add_parser("files", help="shared file store: sheets, views and GLBs stored once by content hash")
    p.add_argument("action", choices=["migrate", "stats", "clean"],
                   help="migrate: store the project's files, sharing identical ones; clean: remove unreferenced files")
    p.add_argument("project")
    p.add_argument("--dry-run", action="store_true", help="clean: only count")
    p.set_defaults(fn=cmd_files)

    p = sub.add_parser("area", help="areas for the daily asset loop: define, status (next step per asset), checklist")
    p.add_argument("action", choices=["define", "list", "status", "checklist"])
    p.add_argument("project")
    p.add_argument("name", nargs="?")
    p.add_argument("--shots", nargs="+", help="define: the shots whose id passes define the area")
    p.add_argument("--districts", nargs="+", help="define: districts (every entry with instances there)")
    p.add_argument("--district", help="status: a district instead of a named area")
    p.set_defaults(fn=cmd_area)

    p = sub.add_parser("terrain", help="world mode: the generated terrain's preset (roll, presets), and the terrain "
                                       "beyond the city fitted to the locked shots")
    p.add_argument("action", choices=["roll", "presets", "analyse", "build", "outer", "apply", "landscape", "ue"],
                   help="roll: a generated terrain from a preset or a description, the city kept; presets: list them; "
                        "analyse: locked shots' skylines; build: ranges from site/terrain_plan/proposal.json, sky fit, "
                        "erosion; outer: only the outer ring (+ its GLB) from the far build; apply: point the layout "
                        "at it; landscape: Unreal tiles + outer ring GLB; ue: auto material + outer ring in the Unreal "
                        "project")
    p.add_argument("project", nargs="?")
    p.add_argument("--preset", help="roll: the terrain preset (ap terrain presets)")
    p.add_argument("--seed", type=int, help="roll: the terrain's own seed (default: a random one)")
    p.add_argument("--describe", help="roll: describe the terrain in words; mapped to a preset and its settings")
    p.add_argument("--replace-heightmap", action="store_true", help="roll: replace a heightmap terrain with the generated one")
    p.set_defaults(fn=cmd_terrain)

    p = sub.add_parser("launcher", help="desktop launcher: a Terraformer entry in the applications menu that opens the UI")
    p.add_argument("action", choices=["install", "remove", "status"])
    p.add_argument("--port", type=int, default=8700, help="install: the UI's port (default 8700)")
    p.set_defaults(fn=cmd_launcher)

    p = sub.add_parser("ui", help="start the local review UI (127.0.0.1 only)")
    p.add_argument("--port", type=int, default=8700)
    p.set_defaults(fn=cmd_ui)

    p = sub.add_parser("vlm", help="local vision LLM server (Qwen3-VL, GPU 0; see config [local])")
    p.add_argument("action", choices=["up", "down", "status"])
    p.add_argument("--model", help="32b | 8b (default: config, falling back to the 8B)")
    p.add_argument("--gpu", type=int, default=None,
                   help="up: GPU to use (default 0); down: only stop it if it's on this GPU")
    p.add_argument("--idle-unload", type=float, default=None,
                   help="free the GPU after this many idle seconds (0 = never; default: config)")
    p.add_argument("--force", action="store_true", help="down: don't wait for a running request")
    p.set_defaults(fn=cmd_vlm)

    p = sub.add_parser("set", help="project settings: vision LLM and per-stage image backend")
    p.add_argument("project")
    p.add_argument("--llm", help="local | gemini | claude")
    p.add_argument("--backend", action="append", help="STAGE=comfyui|gemini (repeatable)")
    p.set_defaults(fn=cmd_set)

    a = ap.parse_args(argv)
    if a.cmd == "vlm":
        a.gpu_given, a.gpu = a.gpu is not None, int(config.get("jobs.gpu0_gpu", 0)) if a.gpu is None else a.gpu
    try:
        a.fn(a)
    except (ComfyError, LLMError, PaidAPIBlocked, FileNotFoundError, FileExistsError,
            ValueError, NotImplementedError, RuntimeError) as e:
        sys.exit(f"ap: {e}")


if __name__ == "__main__":
    main()
