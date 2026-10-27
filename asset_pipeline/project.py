"""Project directory on the storage drive: $AP_ROOT/projects/<slug>/ (layout in the plan).

All state lives in JSON files written atomically, so the UI and CLI can share it.
"""
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .schema import Project

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
STARTS = ("city", "blender", "assets")      # Project.start: procedural city, own Blender layout, concept images


def slugify(name: str) -> str:
    """A project folder name from a display name: lowercase ASCII, words joined by hyphens
    ("Harbour Town at Dawn" -> "harbour-town-at-dawn"); "project" when nothing is left."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:56].strip("-")
    return s or "project"


def unique_slug(name: str) -> str:
    """slugify(name), with -2, -3, … appended while a project folder of that name exists."""
    base, n = slugify(name), 1
    slug = base
    while (config.projects_dir() / slug).exists():
        n += 1
        slug = f"{base}-{n}"
    return slug


_UMASK = os.umask(0)
os.umask(_UMASK)


def write_json(path: Path, data) -> None:
    """Write JSON atomically (temp file in the same dir + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o666 & ~_UMASK)  # mkstemp creates 0600; match normal file perms
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text())


class ProjectStore:
    def __init__(self, root: Path):
        self.root = root

    @property
    def project_file(self) -> Path:
        return self.root / "project.json"

    @classmethod
    def create(cls, slug: str | None = None, name: str = "", brief: str = "", start: str | None = None) -> "ProjectStore":
        """A new project. slug None: made from the name (unique_slug). start: STARTS (None: city)."""
        if not slug:
            if not name.strip():
                raise ValueError("give the project a name")
            slug = unique_slug(name)
        if start is not None and start not in STARTS:
            raise ValueError(f"starting point must be one of {', '.join(STARTS)}")
        if not SLUG_RE.match(slug):
            raise ValueError(f"bad project slug {slug!r}: use lowercase letters, digits, - and _")
        store = cls(config.projects_dir() / slug)
        if store.project_file.exists():
            raise FileExistsError(f"project {slug!r} already exists at {store.root}")
        defaults = config.backends().get("defaults", {})
        project = Project(slug=slug, name=name.strip() or slug, brief=brief, start=start,
                          backends=dict(defaults.get("backends", {})),
                          llm=defaults.get("llm", "local"))
        store.save(project)
        return store

    @classmethod
    def open(cls, slug: str) -> "ProjectStore":
        store = cls(config.projects_dir() / slug)
        if not store.project_file.exists():
            raise FileNotFoundError(f"no project {slug!r} at {store.root}")
        return store

    def load(self) -> Project:
        return Project.model_validate(read_json(self.project_file))

    def save(self, project: Project) -> None:
        write_json(self.project_file, project.model_dump(mode="json"))

    def log_run(self, record: dict) -> None:
        """Append one stage run to runs.jsonl (history of backends, inputs, outputs, timing)."""
        record = {"time": datetime.now(timezone.utc).isoformat(), **record}
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "runs.jsonl").open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")
