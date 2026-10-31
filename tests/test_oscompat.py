"""oscompat: the Linux / native Windows differences. The Windows branches that call Windows programs
are covered by the windows-latest CI job; the parsers and command-line builders run everywhere."""
import os
import subprocess
import sys
import time

import pytest

from asset_pipeline import oscompat


def test_split_windows_cmdline_matches_the_c_runtime():
    s = r'"C:\Program Files\ComfyUI\python_embeded\python.exe" -s main.py --port 8188 "a \"b\"" c\\d'
    assert oscompat.split_windows_cmdline(s) == [r"C:\Program Files\ComfyUI\python_embeded\python.exe", "-s", "main.py",
                                                 "--port", "8188", 'a "b"', r"c\\d"]
    assert oscompat.split_windows_cmdline('x "" y') == ["x", "", "y"]
    assert oscompat.split_windows_cmdline(r'"C:\dir\\" next') == ["C:\\dir\\", "next"]


def test_windows_cmdline_quotes_unreal_option_values():
    cmd = [r"C:\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe", r"C:\Unreal Projects\p\P.uproject",
           r"-script=C:\repo\unreal\ap_import.py C:\work\args.json", "-unattended", r"-ExecCmds=py C:\repo\x.py"]
    line = oscompat.windows_cmdline(cmd)
    assert r'"C:\Unreal Projects\p\P.uproject"' in line
    assert r'-script="C:\repo\unreal\ap_import.py C:\work\args.json"' in line
    assert r'-ExecCmds="py C:\repo\x.py"' in line and " -unattended " in line
    assert oscompat.split_windows_cmdline(line)[1] == r"C:\Unreal Projects\p\P.uproject"


def test_parse_netstat_finds_the_listener():
    out = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1012
  TCP    127.0.0.1:8188         0.0.0.0:0              LISTENING       4242
  TCP    127.0.0.1:8188         127.0.0.1:50000        ESTABLISHED     4242
  TCP    [::]:8700              [::]:0                 LISTENING       77
"""
    assert oscompat.parse_netstat(out, 8188) == 4242
    assert oscompat.parse_netstat(out, 8700) == 77
    assert oscompat.parse_netstat(out, 8000) is None


def test_atomic_write_replaces_in_place(tmp_path):
    f = tmp_path / "a" / "x.json"
    oscompat.atomic_write_text(f, "one")
    oscompat.atomic_write_text(f, "twö")
    assert f.read_text(encoding="utf-8") == "twö"
    assert [p.name for p in f.parent.iterdir()] == ["x.json"]


def test_link_and_symlink_fall_back_to_copies(tmp_path, monkeypatch):
    src = tmp_path / "src.bin"
    src.write_bytes(b"data")
    oscompat.link_or_copy(src, tmp_path / "hard.bin")
    assert (tmp_path / "hard.bin").read_bytes() == b"data"

    def no(*a, **k):
        raise OSError("not on this drive")
    monkeypatch.setattr(os, "link", no)
    oscompat.link_or_copy(src, tmp_path / "copy.bin")
    assert (tmp_path / "copy.bin").read_bytes() == b"data" and os.stat(tmp_path / "copy.bin").st_nlink == 1
    monkeypatch.setattr(type(tmp_path), "symlink_to", no)
    oscompat.symlink_or_copy("src.bin", tmp_path / "sym.bin")
    assert (tmp_path / "sym.bin").read_bytes() == b"data" and not (tmp_path / "sym.bin").is_symlink()


def test_venv_bin_and_exe_names():
    from pathlib import Path
    if oscompat.IS_WINDOWS:
        assert oscompat.venv_bin(Path("env")) == Path("env/Scripts/python.exe")
        assert oscompat.exe("blender") == "blender.exe"
    else:
        assert oscompat.venv_bin(Path("env"), "ap") == Path("env/bin/ap")
        assert oscompat.exe("blender") == "blender"


def test_stop_tree_ends_a_detached_child_and_its_children():
    code = ("import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "time.sleep(60)")
    proc = subprocess.Popen([sys.executable, "-c", code], **oscompat.detached())
    time.sleep(0.5)
    assert oscompat.alive(proc.pid)
    oscompat.stop_popen(proc, wait_s=10)
    assert proc.poll() is not None
    t0 = time.time()
    while oscompat.group_alive(proc.pid) and time.time() - t0 < 5:
        time.sleep(0.1)
    assert not oscompat.group_alive(proc.pid)
    with pytest.raises(ProcessLookupError):
        oscompat.kill_tree(proc.pid)


def test_run_git_without_git(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(subprocess, "run", missing)
    r = oscompat.run_git(["status"], capture_output=True, text=True)
    assert r.returncode == 127 and r.stdout == ""


def test_native_windows_gates(monkeypatch):
    """On native Windows TRELLIS and the vision model refuse with the WSL pointer, and Doctor turns
    their features off with the same line."""
    from asset_pipeline import doctor, vlm
    from asset_pipeline.stages import s5_3d
    monkeypatch.setattr(oscompat, "IS_WINDOWS", True)
    with pytest.raises(vlm.VLMError, match="WSL install"):
        vlm.up()
    with pytest.raises(RuntimeError, match="WSL install"):
        s5_3d.run(None, "plan", "asset")
    checks = {c.id: c for c in doctor.check_local()}
    assert {"vlm", "trellis", "segdepth"} <= set(checks)
    feats = doctor.features(list(checks.values()))
    assert not feats["vlm"]["ok"] and not feats["trellis"]["ok"] and "WSL install" in feats["trellis"]["why"]
    assert feats["blender"]["ok"]
