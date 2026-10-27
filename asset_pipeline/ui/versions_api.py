"""Routes of the asset version bank (asset_pipeline/versions.py), Tools › Storage… and Settings….

    GET     /api/projects/{slug}/library/asset/{asset}/versions          the strip (migrates lazily)
    GET     .../versions/compare?a=&b=                                   two versions side by side
    POST    .../versions/{n}/restore    job library.restore (cpu)        brief back (+ rebuild) / TRELLIS result current
    DELETE  .../versions/{n}?confirm_pinned=true                     (pinned: marked Keep in an earlier UI)
    POST    .../versions/prune {dry_run}                                 versions beyond the project's "keep"
    GET     /api/projects/{slug}/storage                                 totals per asset and project
    POST    /api/projects/{slug}/storage/prune {asset?, dry_run}         delete all but the Active, Approved (and earlier pinned) versions
    POST    /api/projects/{slug}/storage/clean {dry_run}                 shared-store files nothing references
    POST    /api/projects/{slug}/storage/loose                           remove builds that are no version
    GET     /api/projects/{slug}/disk?dirty=                             size on disk by category (toolbar, Properties)
    GET/PUT /api/projects/{slug}/settings                                {versions_keep}
TRELLIS-route props use the same routes: their versions are the TRELLIS results.
"""
from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel

from .. import disk_usage, filestore, versions


class CleanupPick(BaseModel):
    glb: str


class StorageCleanReq(BaseModel):
    dry_run: bool = False


class PruneReq(BaseModel):
    dry_run: bool = True


class StoragePruneReq(BaseModel):
    asset: str | None = None
    dry_run: bool = False


class SettingsReq(BaseModel):
    versions_keep: int


def register(app: FastAPI, jobs, store_of) -> None:
    base = "/api/projects/{slug}/library/asset/{asset}/versions"
    disk = disk_usage.Cache(store_of)
    app.state.disk_usage = disk

    def _call(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'\""))
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        except PermissionError as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get(base)
    def versions_list(slug: str, asset: str):
        return _call(versions.listing, store_of(slug), asset)

    @app.get(base + "/compare")
    def versions_compare(slug: str, asset: str, a: int, b: int):
        return _call(versions.compare, store_of(slug), asset, a, b)

    @app.post(base + "/prune")
    def versions_prune(slug: str, asset: str, req: PruneReq):
        return _call(versions.prune, store_of(slug), asset, dry_run=req.dry_run)

    @app.post("/api/projects/{slug}/library/asset/{asset}/cleanups/use")
    def cleanup_use(slug: str, asset: str, req: CleanupPick):
        """Use this version on a Cleaned card: the city build takes this Clean up output for its tier."""
        from ..stages import s6_cleanup
        store = store_of(slug)
        return _call(s6_cleanup.choose, store, versions.PLAN, asset, req.glb)

    @app.post("/api/projects/{slug}/library/asset/{asset}/cleanups/delete")
    def cleanup_delete(slug: str, asset: str, req: CleanupPick):
        """Delete a Clean up output; the asset goes back to its raw model first when it was in use."""
        from ..stages import s6_cleanup
        store = store_of(slug)
        r = _call(s6_cleanup.delete, store, versions.PLAN, asset, req.glb)
        versions.forget_cleaned(store, asset, req.glb)
        return r

    @app.post(base + "/{n}/restore")
    def versions_restore(slug: str, asset: str, n: int):
        store = store_of(slug)
        _call(versions.get, store, asset, n)
        return jobs.submit("library.restore", slug, lambda: versions.restore(store, asset, n), lane="cpu",
                           tag={"asset": asset, "version": n}).public()

    @app.post(base + "/{n}/smooth")
    def versions_smooth(slug: str, asset: str, n: int):
        """A TRELLIS version welded and shaded smooth by angle, as a new version (smooth.py)."""
        from .. import smooth
        store = store_of(slug)
        r = _call(versions.get, store, asset, n)
        if r["kind"] != "trellis" or smooth.is_smoothed(store, r):
            raise HTTPException(400, f"v{n} is not an unsmoothed TRELLIS model")
        return jobs.submit("library.smooth", slug, lambda: smooth.smooth_version(store, asset, n), lane="cpu",
                           tag={"asset": asset, "version": n}).public()

    @app.post(base + "/{n}/ground")
    def versions_ground(slug: str, asset: str, n: int):
        """A TRELLIS version with its ground slab removed, as a new version (smooth.py); none when
        there is no ground (the version then says so)."""
        from .. import smooth
        store = store_of(slug)
        r = _call(versions.get, store, asset, n)
        if r["kind"] != "trellis" or smooth.ground_checked(store, r):
            raise HTTPException(400, f"v{n} is not a TRELLIS model whose ground is still to look for")
        return jobs.submit("library.ground", slug, lambda: smooth.ground_version(store, asset, n), lane="cpu",
                           tag={"asset": asset, "version": n}).public()

    @app.get("/api/projects/{slug}/library/smooth")
    def smooth_candidates(slug: str):
        from .. import smooth
        store = store_of(slug)
        return {"assets": smooth.candidates(store), "ground": smooth.candidates(store, op="ground"),
                "angle": smooth.angle()}

    @app.post("/api/projects/{slug}/library/smooth")
    def smooth_all(slug: str, body: dict = Body(default={})):
        """Every asset whose Active version is a TRELLIS model still to smooth ({"smooth": true}, the
        default) and/or to remove the ground from ({"ground": true}): one job, one pass and one new
        version per asset, smoothing first."""
        from .. import smooth
        from ..jobs import current_job
        sm, gr = bool(body.get("smooth", True)), bool(body.get("ground", False))
        store = store_of(slug)
        todo = list(dict.fromkeys((smooth.candidates(store) if sm else []) + (smooth.candidates(store, op="ground") if gr else [])))
        if not todo:
            raise HTTPException(400, "no TRELLIS model in use needs that")

        def run():
            job = current_job()
            return smooth.fix_all(store, todo, log=(lambda m: setattr(job, "note", m[:200])) if job else print,
                                  smooth=sm, ground=gr)
        return jobs.submit("library.smooth_all", slug, run, lane="cpu",
                           tag={"assets": todo, "smooth": sm, "ground": gr}).public()

    @app.delete(base + "/{n}")
    def versions_delete(slug: str, asset: str, n: int, confirm_pinned: bool = False):
        return _call(versions.delete, store_of(slug), asset, n, confirm_pinned=confirm_pinned)

    @app.get("/api/projects/{slug}/storage")
    def storage(slug: str):
        store = store_of(slug)
        loose = versions.loose_files(store)
        return versions.storage(store) | {"files": filestore.stats(store), "loose": loose,
                                          "loose_bytes": sum(r["bytes"] for r in loose)}

    @app.get("/api/projects/{slug}/disk")
    def disk_get(slug: str, dirty: bool = False):
        """The last measurement at once (computed in the background, at most every 30 s);
        dirty=true after a job finished or a clean: measure again once the interval is up."""
        store_of(slug)
        return disk.get(slug, dirty=dirty)

    @app.post("/api/projects/{slug}/storage/loose")
    def storage_loose(slug: str):
        return versions.remove_loose(store_of(slug))

    @app.post("/api/projects/{slug}/storage/clean")
    def storage_clean(slug: str, req: StorageCleanReq):
        return filestore.clean(store_of(slug), dry_run=req.dry_run)

    @app.post("/api/projects/{slug}/storage/prune")
    def storage_prune(slug: str, req: StoragePruneReq):
        store = store_of(slug)
        if req.dry_run:
            s = versions.storage(store)
            rows = [r for r in s["assets"] if not req.asset or r["asset"] == req.asset]
            return {"removed": sum(r["removable"] for r in rows), "bytes": sum(r["removable_bytes"] for r in rows)}
        return versions.storage_prune(store, req.asset)

    @app.get("/api/projects/{slug}/settings")
    def settings_get(slug: str):
        return versions.settings(store_of(slug))

    @app.put("/api/projects/{slug}/settings")
    def settings_put(slug: str, req: SettingsReq):
        store = store_of(slug)
        _call(versions.set_keep, store, req.versions_keep)
        return versions.settings(store)
