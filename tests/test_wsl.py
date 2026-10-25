"""Windows (WSL2) + NVIDIA, beta: WSL detection, Windows paths in Setup, Windows Blender/Unreal through
interop, the launcher's WSL side. wslpath and the Windows programs are mocked: nothing here needs Windows."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from asset_pipeline import blender, config, doctor, gpu, profiles, setup, ue, wsl

REPO = Path(__file__).resolve().parents[1]
UNC = "\\\\wsl.localhost\\Ubuntu"
REAL_IS_WSL = wsl.is_wsl


@pytest.fixture
def on_wsl(monkeypatch, tmp_path):
    """Inside WSL with a mocked wslpath: Linux /x/y <-> \\\\wsl.localhost\\Ubuntu\\x\\y, <tmp>/mnt/c/... <-> C:\\..."""
    drives = tmp_path / "mnt"
    (drives / "c").mkdir(parents=True)
    monkeypatch.setattr(wsl, "is_wsl", lambda proc=None: True)
    monkeypatch.setattr(wsl, "is_wsl2", lambda proc=None: True)
    monkeypatch.setattr(wsl, "drive_root", lambda: str(drives))
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    calls = []

    def fake_wslpath(flag, p):
        calls.append((flag, p))
        if flag == "-w":
            if p.startswith(str(drives) + "/"):
                rest = p[len(str(drives)) + 1:]
                return f"{rest[0].upper()}:\\" + rest[2:].replace("/", "\\")
            return UNC + p.replace("/", "\\")
        if p[1:3] in (":\\", ":/"):
            return f"{drives}/{p[0].lower()}/" + p[3:].replace("\\", "/")
        return p[len(UNC):].replace("\\", "/") if p.startswith(UNC) else None
    monkeypatch.setattr(wsl, "_wslpath", fake_wslpath)
    return {"drives": drives, "calls": calls}


def test_is_wsl_reads_the_kernel(tmp_path, monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    (tmp_path / "sys/kernel").mkdir(parents=True)
    (tmp_path / "sys/kernel/osrelease").write_text("6.8.0-45-generic\n")
    (tmp_path / "version").write_text("Linux version 6.8.0-45-generic\n")
    assert not REAL_IS_WSL(tmp_path) and not wsl.is_wsl2(tmp_path)
    (tmp_path / "sys/kernel/osrelease").write_text("5.15.167.4-microsoft-standard-WSL2\n")
    assert REAL_IS_WSL(tmp_path) and wsl.is_wsl2(tmp_path)
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-24.04")
    (tmp_path / "sys/kernel/osrelease").write_text("6.8.0-45-generic\n")
    assert REAL_IS_WSL(tmp_path) and wsl.distro() == "Ubuntu-24.04"


def test_windows_paths_both_ways_with_wslpath(on_wsl):
    c = on_wsl["drives"] / "c"
    assert wsl.is_windows_path("C:\\Program Files\\x.exe") and wsl.is_windows_path("D:/x") and wsl.is_windows_path(UNC + "\\home")
    assert not wsl.is_windows_path("/home/x") and not wsl.is_windows_path("blender") and not wsl.is_windows_path("")
    assert wsl.to_linux('"C:\\Program Files\\Blender Foundation\\blender.exe"') == f"{c}/Program Files/Blender Foundation/blender.exe"
    assert wsl.to_linux(UNC + "\\home\\me\\a.json") == "/home/me/a.json"
    assert wsl.to_linux("/already/linux") == "/already/linux"
    assert wsl.to_windows(f"{c}/Users/me") == "C:\\Users\\me"
    assert wsl.on_windows_drive(f"{c}/Users") and not wsl.on_windows_drive("/home/me") and not wsl.on_windows_drive(on_wsl["drives"])


def test_windows_paths_without_wslpath(monkeypatch, tmp_path):
    monkeypatch.setattr(wsl.shutil, "which", lambda name: None)
    monkeypatch.setattr(wsl, "drive_root", lambda: "/drv")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-24.04")
    assert wsl.to_linux("C:\\Users\\me\\") == "/drv/c/Users/me"
    assert wsl.to_linux("//wsl$/Ubuntu-24.04/home/me") == "/home/me"
    assert wsl.to_windows("/drv/d/Games/x") == "D:\\Games\\x"
    assert wsl.to_windows(str(tmp_path / "new" / "out.png")) == "\\\\wsl.localhost\\Ubuntu-24.04" + str(tmp_path / "new" / "out.png").replace("/", "\\")


def test_wslpath_is_called_with_its_flags(monkeypatch):
    calls = []
    monkeypatch.setattr(wsl.shutil, "which", lambda name: "/usr/bin/wslpath")
    monkeypatch.setattr(wsl.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or
                        subprocess.CompletedProcess(cmd, 0, stdout="C:\\x\n", stderr=""))
    assert wsl.to_windows("/") == "C:\\x" and calls == [["wslpath", "-w", "/"]]


def test_a_nonexistent_output_still_converts(on_wsl, tmp_path):
    out = tmp_path / "out" / "frame.png"
    assert wsl.to_windows(out) == UNC + str(out).replace("/", "\\")
    assert on_wsl["calls"][0] == ("-w", str(out))


def test_windows_cmd_translates_args_job_files_and_env(on_wsl, tmp_path):
    exe = on_wsl["drives"] / "c" / "Program Files" / "Blender Foundation" / "Blender 4.2" / "blender.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    script = REPO / "scripts" / "blender_kit.py"
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"out": str(out_dir / "a.glb"), "asset": "/Game/AP/Meshes/a", "n": 3,
                               "list": [str(tmp_path), "plain"]}))
    args_env = tmp_path / "ue_args.json"
    args_env.write_text(json.dumps({"report": str(tmp_path / "rep.json")}))
    env = {"PATH": "/usr/bin", "AP_UE_ARGS": str(args_env), "WSLENV": "FOO/u"}
    cmd, env2, watch, copies = wsl.windows_cmd(
        [exe, "-b", "--factory-startup", "--python", script, "--", job, f"-script={script} {job}", "-ExecCmds=py " + str(script),
         "-graphicsadapter=1"], env)
    w = lambda p: UNC + str(p).replace("/", "\\")                       # noqa: E731
    assert cmd[0] == str(exe) and cmd[1:4] == ["-b", "--factory-startup", "--python"]
    assert cmd[4] == w(script) and cmd[6] == w(tmp_path / "job.win.json")
    assert cmd[7] == f"-script={w(script)} {w(tmp_path / 'job.win.json')}" and cmd[8] == f"-ExecCmds=py {w(script)}"
    assert cmd[9] == "-graphicsadapter=1"
    win = json.loads((tmp_path / "job.win.json").read_text())
    assert win == {"out": w(out_dir / "a.glb"), "asset": "/Game/AP/Meshes/a", "n": 3, "list": [w(tmp_path), "plain"]}
    assert json.loads(job.read_text())["out"] == str(out_dir / "a.glb")          # the original stays Linux
    assert env2["AP_UE_ARGS"] == str(tmp_path / "ue_args.win.json") and env2["WSLENV"] == "FOO/u:AP_UE_ARGS/p"
    assert json.loads((tmp_path / "ue_args.win.json").read_text())["report"] == w(tmp_path / "rep.json")
    assert out_dir in watch and tmp_path in watch
    assert set(copies) == {tmp_path / "job.win.json", tmp_path / "ue_args.win.json"}


def test_linux_programs_are_left_alone(on_wsl, tmp_path):
    cmd = ["blender", "-b", "--python", str(tmp_path / "x.py")]
    assert wsl.windows_cmd(cmd, {"A": "1"}) == (cmd, {"A": "1"}, [], [])
    assert not wsl.is_windows_exe("/usr/bin/blender")


def test_read_back_turns_windows_paths_into_linux_ones(on_wsl, tmp_path):
    t = time.time()
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"p": "C:\\keep"}))
    os.utime(old, (t - 100, t - 100))
    (tmp_path / "sub").mkdir()
    rep = tmp_path / "sub" / "report.json"
    rep.write_text(json.dumps({"glb": UNC + str(tmp_path / "a.glb").replace("/", "\\"), "tex": "C:\\Users\\me\\t.png",
                               "ok": True, "name": "C:"}))
    copy = tmp_path / "job.win.json"
    copy.write_text("{}")
    done = wsl.read_back([tmp_path], t, [copy])
    assert done == [rep] and not copy.exists()
    assert json.loads(rep.read_text()) == {"glb": str(tmp_path / "a.glb"), "tex": f"{on_wsl['drives']}/c/Users/me/t.png",
                                           "ok": True, "name": "C:"}
    assert json.loads(old.read_text()) == {"p": "C:\\keep"}                  # older than the run: not touched


FAKE_WIN_BLENDER = r'''#!{py}
# Stands in for a Windows blender.exe: it only understands Windows paths (\\wsl.localhost\Ubuntu\...)
import json, sys
UNC = "\\\\wsl.localhost\\Ubuntu"
lin = lambda p: p[len(UNC):].replace("\\", "/")
args = sys.argv[sys.argv.index("--") + 1:]
assert all(a.startswith(UNC) for a in args), args
job = json.load(open(lin(args[0])))
assert job["out"].startswith(UNC), job
open(lin(job["out"]), "w").write(json.dumps({{"mesh": job["out"], "ok": True}}))
print("Blender 4.2.0 (fake, Windows)")
'''


def test_blender_run_calls_a_windows_blender_with_windows_paths(on_wsl, tmp_path):
    exe = on_wsl["drives"] / "c" / "Blender" / "blender.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text(FAKE_WIN_BLENDER.format(py=sys.executable))
    exe.chmod(0o755)
    out = tmp_path / "out"
    out.mkdir()
    job = out / "job.json"
    job.write_text(json.dumps({"out": str(out / "result.json")}))
    blender.run(REPO / "scripts" / "blender_kit.py", [job], tmp_path / "blender.log", "fake build", exe=exe)
    assert json.loads((out / "result.json").read_text()) == {"mesh": str(out / "result.json"), "ok": True}   # read back
    assert not (out / "job.win.json").exists() and "Blender 4.2.0" in (tmp_path / "blender.log").read_text()


def test_setup_takes_windows_paths_for_blender_and_unreal(on_wsl, monkeypatch, tmp_path):
    c = on_wsl["drives"] / "c"
    exe = c / "Program Files" / "Blender Foundation" / "Blender 4.2" / "blender.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\necho 'Blender 4.2.3'\r\n")
    exe.chmod(0o755)
    r = setup.test("blender", "C:\\Program Files\\Blender Foundation\\Blender 4.2\\blender.exe")
    assert r["ok"] and r["warn"] and "on Windows" in r["message"] and str(exe) in r["message"]
    ue_root = c / "Program Files" / "Epic Games" / "UE_5.8"
    cmd = ue_root / setup.UE_TAIL_WIN
    cmd.parent.mkdir(parents=True)
    cmd.write_text("")
    cmd.chmod(0o755)
    (ue_root / "Engine" / "Build").mkdir(parents=True)
    (ue_root / "Engine" / "Build" / "Build.version").write_text('{"MajorVersion": 5, "MinorVersion": 8, "PatchVersion": 3}')
    r = setup.test("unreal", "C:\\Program Files\\Epic Games\\UE_5.8")
    assert r["ok"] and "5.8.3 on Windows" in r["message"] and "beta" in r["message"]
    assert ue.full_editor(cmd).name == "UnrealEditor.exe" and ue.full_editor(Path("/x/UnrealEditor-Cmd")).name == "UnrealEditor"
    r = setup.test("unreal_projects", str(tmp_path / "ue"), {"unreal": str(cmd)})
    assert r["warn"] and "Windows drive" in r["message"]
    setup.save({"blender": "C:\\Program Files\\Blender Foundation\\Blender 4.2\\blender.exe"})
    assert config.get("tools.blender") == str(exe)                        # stored as WSL sees it


def test_setup_prefers_a_blender_inside_wsl(on_wsl, monkeypatch, tmp_path):
    win = on_wsl["drives"] / "c" / "Program Files" / "Blender Foundation" / "Blender 4.2" / "blender.exe"
    win.parent.mkdir(parents=True)
    win.write_text("")
    win.chmod(0o755)
    linux = tmp_path / "bin" / "blender"
    linux.parent.mkdir()
    linux.write_text("")
    linux.chmod(0o755)
    config.save_user({"tools": {"blender": str(win)}})
    monkeypatch.setattr(setup.shutil, "which", lambda p: str(linux) if p == "blender" else (p if Path(p).is_file() else None))
    assert setup._blender_candidates()[:2] == [str(linux), str(win)]


def test_storage_on_a_windows_drive_is_explained(on_wsl):
    r = setup.test("storage", "C:\\terraformer")
    assert r["ok"] and r["warn"] and "Windows drive" in r["message"] and "hard links" in r["message"]
    help_ = {i["id"]: i["help"] for i in setup.state()["items"]}
    assert "keep it inside WSL" in help_["storage"] and "C:\\Program Files" in help_["blender"]
    assert setup.state()["wsl"]["label"] == wsl.LABEL


def test_profile_and_doctor_show_the_beta_label(on_wsl, monkeypatch):
    monkeypatch.setattr(gpu, "vendor", lambda: "nvidia")
    monkeypatch.setattr(gpu, "list_gpus", lambda: [{"index": 0, "name": "RTX", "vram_gb": 24.0}])
    hw = profiles.detect()
    assert hw["profile"] == "wsl-nvidia" and hw["wsl"]
    p = profiles.PROFILES["wsl-nvidia"]
    assert p.title == "Windows (WSL2) + NVIDIA, beta" and not p.tested and p.torch == "cu128" and "Untested" in p.note
    config.save_user({"hardware": {"profile": "wsl-nvidia"}})
    assert profiles.allocator_env() == {"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"}
    assert setup.test("hardware_profile", "nvidia")["warn"]
    assert setup.test("hardware_profile", "wsl-nvidia")["warn"]                 # beta: always a note
    checks = {c.id: c for c in doctor.check_wsl()}
    assert checks["wsl"].level == "warn" and wsl.LABEL in checks["wsl"].detail and "Untested" in checks["wsl"].detail
    monkeypatch.setenv("AP_ROOT", str(on_wsl["drives"] / "c" / "tf"))
    assert {c.id for c in doctor.check_wsl()} >= {"wsl", "wsl_storage"}


def test_off_wsl_nothing_changes(monkeypatch):
    monkeypatch.setattr(wsl, "is_wsl", lambda proc=None: False)
    monkeypatch.setattr(gpu, "vendor", lambda: "nvidia")
    monkeypatch.setattr(gpu, "list_gpus", lambda: [{"index": 0, "name": "RTX", "vram_gb": 24.0}])
    assert profiles.detect()["profile"] == "nvidia" and doctor.check_wsl() == []
    assert not setup.test("hardware_profile", "wsl-nvidia")["ok"]


def test_nvidia_smi_from_the_wsl_driver(monkeypatch):
    monkeypatch.setattr(gpu.shutil, "which", lambda name: None)
    monkeypatch.setattr(gpu, "_wsl_nvidia_smi", lambda: True)
    assert gpu.vendor() == "nvidia" and gpu._nvidia_smi() == gpu.WSL_NVIDIA_SMI
    calls = []
    monkeypatch.setattr(gpu.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or
                        subprocess.CompletedProcess(cmd, 0, stdout="0, 24576\n", stderr=""))
    assert gpu.vram_total_gb() == {0: pytest.approx(24576 * 1048576 / 1e9)} and calls[0][0] == gpu.WSL_NVIDIA_SMI


# --- the launcher's WSL side (scripts/wsl_ui.sh) ----------------------------------------------

FAKE_AP = r'''#!/usr/bin/env bash
# stands in for `ap ui --port N`: answers /api/version, records how it was stopped
exec {py} -c '
import http.server, signal, sys, pathlib
mark = pathlib.Path(sys.argv[1])
def bye(sig, _f):
    mark.write_text(signal.Signals(sig).name); sys.exit(0)
signal.signal(signal.SIGTERM, bye); signal.signal(signal.SIGINT, bye)
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"{{}}")
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(sys.argv[2])), H).serve_forever()
' {mark} "$3"
'''


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_wsl_ui_starts_opens_the_browser_and_stops_when_the_window_closes(tmp_path):
    bin_ = tmp_path / "scripts"
    bin_.mkdir()
    for f in ("wsl_ui.sh", "ap_root.sh", "ap_config.py"):
        (bin_ / f).write_text((REPO / "scripts" / f).read_text())
    (bin_ / "wsl_ui.sh").chmod(0o755)
    mark = tmp_path / "stopped"
    (bin_ / "ap").write_text(FAKE_AP.format(py=sys.executable, mark=mark))
    (bin_ / "ap").chmod(0o755)
    fake = tmp_path / "winbin"
    fake.mkdir()
    opened = tmp_path / "opened"
    (fake / "powershell.exe").write_text(f'#!/bin/sh\necho "$@" >> {opened}\n')
    (fake / "powershell.exe").chmod(0o755)
    port = _free_port()
    env = {"PATH": f"{fake}:{os.environ['PATH']}", "HOME": str(tmp_path), "AP_ROOT": str(tmp_path / "root")}
    log = (tmp_path / "console.txt").open("w")
    p = subprocess.Popen(["bash", str(bin_ / "wsl_ui.sh"), str(port)], stdin=subprocess.PIPE, stdout=log,
                         stderr=subprocess.STDOUT, text=True, env=env)
    try:
        for _ in range(100):
            if opened.exists():
                break
            time.sleep(0.1)
        assert f"http://localhost:{port}/" in opened.read_text()
        p.stdin.close()                                      # the launcher window closes
        p.wait(timeout=20)
    finally:
        if p.poll() is None:
            p.send_signal(signal.SIGKILL)
        log.close()
    out = (tmp_path / "console.txt").read_text()
    assert p.returncode == 0, out
    assert mark.read_text() == "SIGTERM" and "Stopping Terraformer" in out


def test_windows_scripts_are_wired_up():
    bat = (REPO / "windows" / "Terraformer.bat").read_text()
    assert 'set "TF_DISTRO=' in bat and 'set "TF_PORT=8700"' in bat and "scripts/wsl_ui.sh" in bat
    ps1 = (REPO / "windows" / "install.ps1").read_text()
    for needle in ("19044", "wsl.exe --install -d", "nvidia-smi", "install.sh --profile wsl-nvidia", "Terraformer.lnk",
                   'GetFolderPath("Programs")', 'GetFolderPath("Desktop")', "Read-Host"):
        assert needle in ps1, needle
    assert "install.ps1" in (REPO / "windows" / "install.bat").read_text()
    attrs = (REPO / ".gitattributes").read_text()
    assert "windows/*.bat text eol=crlf" in attrs and "windows/*.ps1 text eol=crlf" in attrs
    r = subprocess.run(["bash", str(REPO / "scripts" / "install.sh"), "--profile", "nope"], capture_output=True, text=True)
    assert r.returncode == 2 and "wsl-nvidia" in r.stderr
