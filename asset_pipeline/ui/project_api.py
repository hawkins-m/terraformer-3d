"""Routes of File > Manage projects… (asset_pipeline/project_manager.py).

    GET    /api/manage/projects                  every project: facts, protected, sizes (project, Unreal, backups)
    GET    /api/manage/projects/{slug}/delete-plan?current=   what Delete would remove, or why it can't
    POST   /api/manage/projects/{slug}/rename    {name}
    POST   /api/manage/projects/{slug}/duplicate {name?}
    POST   /api/manage/projects/{slug}/protect   {protected}
    POST   /api/manage/projects/{slug}/delete    {unreal, backups, current}: to the system trash
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .. import project_manager as pm


class NameReq(BaseModel):
    name: str = ""


class ProtectReq(BaseModel):
    protected: bool


class DeleteReq(BaseModel):
    unreal: bool = False
    backups: bool = False
    current: str | None = None      # the project open in the page: it can't be deleted


def register(app: FastAPI, jobs, store_of) -> None:
    def busy(slug: str) -> bool:
        return any(j.active for j in jobs.list(slug) if j.project == slug)

    def guard(slug: str):
        store_of(slug)              # 404 for an unknown project

    @app.get("/api/manage/projects")
    def manage_list():
        return pm.listing()

    @app.get("/api/manage/projects/{slug}/delete-plan")
    def manage_plan(slug: str, current: str | None = None):
        guard(slug)
        return pm.plan_delete(slug, current, busy(slug))

    @app.post("/api/manage/projects/{slug}/rename")
    def manage_rename(slug: str, req: NameReq):
        guard(slug)
        try:
            return {"name": pm.rename(slug, req.name)}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/manage/projects/{slug}/duplicate")
    def manage_duplicate(slug: str, req: NameReq):
        guard(slug)
        try:
            return {"slug": pm.duplicate(slug, req.name)}
        except (ValueError, OSError) as e:
            raise HTTPException(400, str(e))

    @app.post("/api/manage/projects/{slug}/protect")
    def manage_protect(slug: str, req: ProtectReq):
        guard(slug)
        return {"protected": pm.set_protected(slug, req.protected)}

    @app.post("/api/manage/projects/{slug}/delete")
    def manage_delete(slug: str, req: DeleteReq):
        guard(slug)
        try:
            return pm.delete(slug, req.unreal, req.backups, req.current, busy(slug))
        except pm.Blocked as e:
            raise HTTPException(409, str(e))
        except RuntimeError as e:
            raise HTTPException(500, str(e))
