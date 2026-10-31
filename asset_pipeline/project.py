"""Project directory on the storage drive: $AP_ROOT/projects/<slug>/ (layout in the plan).

All state lives in JSON files written atomically, so the UI and CLI can share it.
"""
import json
import os
import re
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .schema import DressingOptions, Project, UnrealSettings

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
        if hasattr(os, "fchmod"):           # mkstemp creates 0600; match normal file perms (no fchmod on Windows)
            os.fchmod(fd, 0o666 & ~_UMASK)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_bytes(path: Path, data: bytes) -> None:
    """Write a file atomically (temp file in the same dir + rename): a reader never sees it half
    written (a build reading site/terrain.png while a poll re-saves it read a truncated PNG)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o666 & ~_UMASK)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_image(im, path: Path, unchanged_ok: bool = False, **kw) -> bool:
    """PIL Image.save, atomically (write_bytes); the format comes from the suffix. unchanged_ok:
    leave a file with the same bytes alone (no new mtime). True when it wrote."""
    import io
    buf = io.BytesIO()
    im.save(buf, format=kw.pop("format", None) or _image_format(path), **kw)
    data = buf.getvalue()
    if unchanged_ok and path.is_file() and path.stat().st_size == len(data) and path.read_bytes() == data:
        return False
    write_bytes(path, data)
    return True


def _image_format(path: Path) -> str:
    from PIL import Image
    Image.init()
    return Image.EXTENSION[path.suffix.lower()]


class TruncatedImage(OSError):
    """An image that stays unreadable (truncated or not an image) after load_image's retries."""


def load_image(path: Path, retries: int = 3, delay_s: float = 0.5):
    """PIL Image.open + load, retried a few times when the file is truncated or not yet an image
    (being written by a writer that isn't atomic: another program, an older version). A missing
    file raises FileNotFoundError at once; one still unreadable after the retries TruncatedImage."""
    from PIL import Image
    for i in range(retries + 1):
        try:
            im = Image.open(path)
            im.load()
            return im
        except FileNotFoundError:
            raise
        except OSError as e:                   # "image file is truncated", UnidentifiedImageError
            if i == retries:
                raise TruncatedImage(f"{path}: {e}") from e
            time.sleep(delay_s)


# warnings for the run collecting them (ue_build.build): readers that skip or replace a bad input
# say so here instead of failing the whole run
_WARNINGS: ContextVar[list | None] = ContextVar("ap_warnings", default=None)


@contextmanager
def collecting_warnings():
    """with collecting_warnings() as w: warn() messages in this thread go into the list w."""
    out: list = []
    token = _WARNINGS.set(out)
    try:
        yield out
    finally:
        _WARNINGS.reset(token)


def warn(msg: str) -> None:
    """A warning for the run's report (collect_warnings), else printed."""
    w = _WARNINGS.get()
    if w is None:
        print(f"warning: {msg}")
    elif msg not in w:
        w.append(msg)


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
                          llm=defaults.get("llm", "local"),
                          unreal=UnrealSettings(dressing=DressingOptions(**defaults.get("dressing", {}))))
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
