"""Manage projects from the app: list, rename, duplicate, protect, delete (File > Manage projects…,
`ap project`).

- Delete never removes for good: folders go to the system trash (send2trash: the freedesktop trash on
  Linux, the Recycle Bin on Windows). A project's Unreal project and backups live outside its folder;
  they are offered separately, and only when they are this project's (see unreal_targets).
- "Protected" blocks Delete. It lives in project.json (`protected`); None (never set) means protected
  while the project has approved assets, so existing work is safe without anyone marking it.
- Last opened is a machine fact, not project content: $AP_ROOT/project-meta.json.
"""
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config, disk_usage, filestore, ue
from .project import SLUG_RE, ProjectStore, read_json, unique_slug, write_json

META = "project-meta.json"
OPENED_EVERY_S = 300           # the UI polls a project; its "opened" time moves at most this often


class Blocked(ValueError):
    """Why an action isn't allowed (protected, open, running jobs): shown to the user as is."""


# --- last opened ---------------------------------------------------------------------------------

def _meta_path() -> Path:
    return config.ap_root() / META


def _meta() -> dict:
    try:
        return json.loads(_meta_path().read_text()) or {}
    except (OSError, ValueError):
        return {}


def touch_opened(slug: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    m = _meta()
    if now - (m.get(slug) or {}).get("opened", 0) < OPENED_EVERY_S:
        return
    m.setdefault(slug, {})["opened"] = now
    try:
        write_json(_meta_path(), m)
    except OSError:
        pass                                   # a read-only storage root must not break opening a project


# --- facts about one project -----------------------------------------------------------------------

def has_approved(store: ProjectStore) -> bool:
    from .stages import sw_library
    try:
        revs = (sw_library.decisions(store).get("_reviews") or {}).values()
        if any(r.get("status") == "approved" for r in revs):
            return True
    except (OSError, ValueError, AttributeError):
        pass
    for f in (store.root / "3d").glob("**/versions.json"):
        try:
            if any(r.get("approved") for r in read_json(f, default=[]) or []):
                return True
        except (OSError, ValueError, AttributeError):
            continue
    return False


def is_protected(store: ProjectStore, approved: bool | None = None) -> bool:
    p = store.load().protected
    return p if p is not None else (has_approved(store) if approved is None else approved)


def layout_facts(store: ProjectStore) -> tuple[str, str]:
    """(layout preset, city size) from site/layout.json; ("", "") for a project without a layout."""
    d = read_json(store.root / "site" / "layout.json", default=None)
    city = (d or {}).get("city") or {}
    return ((city.get("preset") or {}).get("name") or ("utopia" if d else ""), city.get("size") or ("standard" if d else ""))


def _tree_bytes(path: Path, seen: set) -> int:
    n = 0
    for _, st in disk_usage._walk(path):
        k = (st.st_dev, st.st_ino)
        if k not in seen:
            seen.add(k)
            n += disk_usage._on_disk(st)
    return n


def unreal_targets(store: ProjectStore) -> dict:
    """{"project": {path, exists, reason}, "backups": {…}}: the Unreal folders that are this project's.
    `reason` (non-empty) says why one isn't offered for deletion: a scratch project that would get its
    Unreal folders from the user's config, a folder another pipeline project owns, or one that is the
    projects or home folder itself."""
    out = {}
    try:
        paths = {"project": ue.project_dir(store), "backups": ue.project_backups_dir(store)}
    except Exception as e:  # noqa: BLE001
        return {k: {"path": "", "exists": False, "reason": f"Unreal settings unreadable: {e}"} for k in ("project", "backups")}
    own_dir, own_bak = ue._own(store)
    user_root = (config.user().get("paths") or {}).get("ap_root")
    scratch = bool(user_root) and Path(user_root).expanduser().resolve() != config.ap_root().resolve()
    for key, own, env in (("project", own_dir, "AP_UE_PROJECTS"), ("backups", own_bak, "AP_UE_BACKUPS")):
        p, reason = paths[key], ""
        if scratch and not own and not os.environ.get(env):
            reason = "this project is in another storage folder but its Unreal folder comes from your user config"
        elif p.resolve() in (Path.home().resolve(), config.projects_dir().resolve(), config.ap_root().resolve(), Path(p.anchor)):
            reason = "that folder is shared, not this project's"
        elif key == "project" and (p / ue.OWNER_FILE).is_file():
            owner = (read_json(p / ue.OWNER_FILE, default={}) or {}).get("project", "")
            if owner and owner != str(store.root.resolve()):
                reason = f"it belongs to the pipeline project {owner}"
        out[key] = {"path": str(p), "exists": p.is_dir(), "reason": reason}
    return out


def info(slug: str, sizes: bool = True) -> dict:
    store = ProjectStore.open(slug)
    proj = store.load()
    approved = has_approved(store)
    preset, size = layout_facts(store)
    row = {"slug": slug, "name": proj.name or slug, "preset": preset, "size": size,
           "approved": approved, "protected": is_protected(store, approved),
           "protected_set": proj.protected, "opened": (_meta().get(slug) or {}).get("opened"),
           "created": proj.created.isoformat()}
    ut = unreal_targets(store)
    row["unreal"] = ut
    if sizes:
        seen: set = set()
        row["bytes"] = {"project": _tree_bytes(store.root, seen),
                        **{k: (_tree_bytes(Path(ut[u]["path"]), seen) if ut[u]["exists"] else 0)
                           for k, u in (("unreal", "project"), ("backups", "backups"))}}
    return row


def listing(sizes: bool = True) -> list[dict]:
    root = config.projects_dir()
    slugs = sorted(p.name for p in root.iterdir() if (p / "project.json").is_file()) if root.is_dir() else []
    out = []
    for s in slugs:
        try:
            out.append(info(s, sizes))
        except Exception as e:  # noqa: BLE001  (one broken project must not hide the others)
            out.append({"slug": s, "name": s, "error": str(e)})
    return out


# --- actions ---------------------------------------------------------------------------------------

def set_protected(slug: str, value: bool) -> bool:
    store = ProjectStore.open(slug)
    p = store.load()
    p.protected = bool(value)
    store.save(p)
    return p.protected


def rename(slug: str, name: str) -> str:
    """The display name only: the folder id (slug) stays."""
    name = (name or "").strip()
    if not name:
        raise ValueError("give the project a name")
    store = ProjectStore.open(slug)
    p = store.load()
    p.name = name
    store.save(p)
    return name


def _link_aware_copy(src: Path, dst: Path) -> None:
    """copytree that keeps hard links between files inside the tree (the shared file store), so a
    duplicate doesn't double the disk size."""
    made: dict = {}

    def copy(s, d):
        st = os.lstat(s)
        k = (st.st_dev, st.st_ino)
        if st.st_nlink > 1 and k in made:
            try:
                os.link(made[k], d)
                return d
            except OSError:
                pass
        shutil.copy2(s, d)
        if not os.access(d, os.W_OK):
            os.chmod(d, filestore.RW)
        if st.st_nlink > 1:
            made[k] = d
        return d
    shutil.copytree(src, dst, copy_function=copy, symlinks=True)


def duplicate(slug: str, name: str = "") -> str:
    """A copy of the project folder as a new project. It does not share the original's Unreal project
    or backups: its own unreal.project_dir / backup_dir are cleared, so it gets folders of its own."""
    src = ProjectStore.open(slug)
    proj = src.load()
    new_name = (name or "").strip() or f"{proj.name or slug} copy"
    new = unique_slug(new_name)
    if not SLUG_RE.match(new):
        new = unique_slug("project copy")
    dst = config.projects_dir() / new
    _link_aware_copy(src.root, dst)
    store = ProjectStore(dst)
    p = store.load()
    p.slug, p.name, p.protected = new, new_name, None
    p.created = datetime.now(timezone.utc)
    if p.unreal:
        p.unreal.project_dir = p.unreal.backup_dir = ""
    store.save(p)
    return new


def _trash(path: Path) -> None:
    """To the system trash, never a hard delete; if there is no trash, it fails and nothing is removed."""
    try:
        from send2trash import send2trash
    except ImportError as e:
        raise RuntimeError("the send2trash package is missing (pip install send2trash): nothing was deleted") from e
    try:
        send2trash(os.fspath(path.resolve()))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"couldn't move {path} to the trash ({e}): nothing else was deleted") from e


def plan_delete(slug: str, current: str | None = None, busy: bool = False) -> dict:
    """What Delete would remove and the space that frees, or why it can't be done."""
    row = info(slug, sizes=True)
    blocked = ""
    if current == slug:
        blocked = "This project is open. Switch to another project first, then delete it."
    elif row.get("protected"):
        blocked = "This project is protected. Turn Protected off first if you really want to delete it."
    elif busy:
        blocked = "Jobs are running for this project. Stop them first."
    items = [{"key": "project", "label": "Project folder", "path": str(config.projects_dir() / slug),
              "bytes": row["bytes"]["project"], "always": True, "exists": True, "reason": ""}]
    for key, label, bkey in (("unreal", "Unreal project", "project"), ("backups", "Unreal backups", "backups")):
        t = row["unreal"][bkey]
        items.append({"key": key, "label": label, "path": t["path"],
                      "bytes": row["bytes"][key], "always": False, "exists": t["exists"], "reason": t["reason"]})
    return {"slug": slug, "name": row["name"], "blocked": blocked, "items": items}


def delete(slug: str, unreal: bool = False, backups: bool = False, current: str | None = None,
           busy: bool = False) -> dict:
    """Move the project folder (and, when asked and it is this project's, its Unreal project and
    backups) to the trash. Unreal folders go first, so a failure there leaves the project intact."""
    plan = plan_delete(slug, current, busy)
    if plan["blocked"]:
        raise Blocked(plan["blocked"])
    by = {i["key"]: i for i in plan["items"]}
    todo = []
    for key, want in (("unreal", unreal), ("backups", backups)):
        i = by[key]
        if want and i["exists"]:
            if i["reason"]:
                raise Blocked(f"Won't delete the {i['label'].lower()}: {i['reason']}.")
            todo.append(i)
    todo.append(by["project"])
    freed = 0
    for i in todo:
        _trash(Path(i["path"]))
        freed += i["bytes"]
    meta = _meta()
    if meta.pop(slug, None) is not None:
        write_json(_meta_path(), meta)
    return {"slug": slug, "removed": [i["label"] for i in todo], "freed": freed}
