#!/usr/bin/env python3
"""Screenshots of every setup wizard page for the README, on a synthetic machine (2 ROCm GPUs,
nothing installed, canned Test results, a fake install halfway through its downloads).

    .venv/bin/python scripts/installer_screenshots.py [--out docs/images/installer] [--assets DIR]

Starts the wizard's real server with that synthetic state, then drives it with Playwright in node
(scripts/installer_screenshots.mjs; needs `npm i playwright` or a global Playwright) at 1024x768:
page-01.png ... page-09.png (page 8 mid-install, with the concept video playing) and all-pages.png,
a contact sheet. --assets points at another art folder (default installer/assets). Nothing is
downloaded or installed and your own config is never read: everything runs in a temp folder.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

GPUS = [{"index": 0, "name": "AMD Radeon AI PRO R9700", "vram_gb": 31.9},
        {"index": 1, "name": "AMD Radeon AI PRO R9700", "vram_gb": 31.9}]
TESTS = {           # canned; the ComfyUI items get the wizard's own answer (no ComfyUI here: Setup installs one)
    "storage": (True, False, "Works: {v} will be created, 812 GB free."),
    "comfy_backend": (True, False, "sdk: Comfy SDK through the local proxy (test the proxy below)."),
    "blender": (True, False, "Works: Blender 5.2.2 ({v})."),
    "unreal": (True, True, "Not set: the Unreal tab stays off; everything else works."),
}


def synthetic(tmp: Path, assets: Path | None):
    os.environ["AP_CONFIG_DIR"] = str(tmp / "config")
    os.environ["AP_ROOT"] = str(tmp / "storage" / "terraformer")
    from asset_pipeline import config, gpu, installer, models, profiles, setup, wsl
    config.backends.cache_clear()
    gpu.list_gpus = lambda: GPUS
    gpu.vendor = lambda: "amd"
    wsl.is_wsl = lambda proc=None: False
    profiles.detect = lambda: {"vendor": "amd", "count": 2, "gpus": GPUS, "profile": "rocm-2gpu", "wsl": False}
    det = setup.detect
    setup.detect = lambda: {k: v for k, v in det().items() if not k.startswith("comfy_") or k == "comfy_backend"} | {
        "blender": "/usr/bin/blender", "storage": "~/.local/share/terraformer"}
    setup.first_run = lambda: True
    state = setup.state
    setup.state = lambda: state() | {"items": [i | ({"value": "~/.local/share/terraformer"} if i["id"] == "storage" else {})
                                               for i in state()["items"]]}
    setup.test = lambda item_id, value=None, values=None: (
        {"id": item_id, "ok": TESTS[item_id][0], "warn": TESTS[item_id][1], "message": TESTS[item_id][2].format(v=value)}
        if item_id in TESTS else {"id": item_id, "ok": False, "warn": False, "message": "Nothing answers."})
    installer.comfy_found = lambda: {"found": False, "where": ""}
    installer.free_bytes = lambda path: 812 * 10**9
    if assets:
        installer.ASSETS = assets
    vlm = models.get("vlm-32b")

    class Demo(installer.Installer):
        """Install halfway: the 32B vision model downloading, then done when asked."""
        def start(self, secrets=None):
            with self.lock:
                vals, sel = self.st["values"], self.st["components"] or []
                steps = installer.plan(vals, sel)
                at = next(i for i, s in enumerate(steps) if s["kind"] == "models")
                for s in steps[:at]:
                    s["status"] = "done"
                dl = steps[at]
                dl["status"] = "running"
                dl["done"] = int(dl["weight"] * 0.52)
                self.st["install"] = {"status": "running", "steps": steps, "errors": [],
                                      "current": {"label": f"Downloading {vlm.title}", "model": vlm.id,
                                                  "done": int(vlm.size * 0.508), "total": vlm.size},
                                      "download": {"done": dl["done"], "total": dl["weight"]}}
                return self.progress()

        def finish_demo(self):
            with self.lock:
                for s in self.ins["steps"]:
                    s["status"] = "done"
                self.ins.update(status="done", current=None)

    return installer, Demo()


def contact_sheet(out: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont
    names = [f"page-{i:02d}.png" for i in range(1, 10)]
    w, h, pad, label = 512, 384, 16, 22
    sheet = Image.new("RGB", (3 * w + 4 * pad, 3 * (h + label) + 4 * pad), (212, 208, 200))
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 14)
    except OSError:
        font = ImageFont.load_default()
    titles = ["1 Welcome", "2 License", "3 Hardware profile", "4 Components", "5 Destination", "6 Connections",
              "7 Ready to install", "8 Installing", "9 Finish"]
    for i, n in enumerate(names):
        x, y = pad + (i % 3) * (w + pad), pad + (i // 3) * (h + label + pad)
        d.text((x, y), titles[i], fill=(0, 0, 0), font=font)
        sheet.paste(Image.open(out / n).convert("RGB").resize((w, h), Image.LANCZOS), (x, y + label))
    sheet.save(out / "all-pages.png", optimize=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(REPO / "docs" / "images" / "installer"))
    ap.add_argument("--assets", default="", help="art folder (default installer/assets)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        installer, demo = synthetic(Path(td), Path(a.assets).resolve() if a.assets else None)
        state = {"profile": ""}
        httpd = installer.Server(("127.0.0.1", 0), installer.make_handler(demo, state))
        state["httpd"] = httpd
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        url = f"http://localhost:{httpd.server_address[1]}/"
        drv = subprocess.Popen(["node", str(REPO / "scripts" / "installer_screenshots.mjs"), url, str(out)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        for line in drv.stdout:                    # the driver asks for the install to finish
            print(line.rstrip())
            if line.strip() == "finish-install":
                demo.finish_demo()
                drv.stdin.write("ok\n")
                drv.stdin.flush()
        if drv.wait():
            raise SystemExit("installer_screenshots.mjs failed")
        httpd.shutdown()
    contact_sheet(out)
    print(json.dumps(sorted(p.name for p in out.glob("*.png"))))


if __name__ == "__main__":
    main()
