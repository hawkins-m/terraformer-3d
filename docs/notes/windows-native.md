# Native Windows (beta)

Native Windows (no WSL) runs the greybox path: `ap ui`, Layout and its 3D viewer, Greybox (Windows
Blender), Shots, Frames through a Windows ComfyUI, and Build city in Unreal (UnrealEditor-Cmd.exe, with
dressing). Added 2026-10-31. Nothing has run on a real Windows PC yet; the windows-latest CI job runs
the unit tests. The tester checklist is [docs/windows-native-test.md](../windows-native-test.md).

- **One module holds the OS differences: `asset_pipeline/oscompat.py`.** Process groups (POSIX
  sessions / CREATE_NEW_PROCESS_GROUP), `kill_tree`/`stop_tree`/`stop_popen` (killpg / `taskkill /T`),
  `alive`, `process_name`/`process_argv` (/proc / tasklist and PowerShell CIM), `port_owner` (ss /
  netstat), hard-link and symlink fallbacks to copies, `atomic_write_text` (os.replace), `venv_bin`
  (bin/ vs Scripts\*.exe), config and data folders (XDG / %APPDATA%, %LOCALAPPDATA%), `run_git`
  (git may be missing), `windows_cmdline` and `short_path`. Every POSIX branch is the old behaviour.
  New code that starts, stops or inspects processes goes through it, never `os.killpg` or /proc directly.
- **Unreal's command line on Windows:** a quoted `"-ExecCmds=py x"` makes UE read only up to the first
  space, so `blender.run_logged` builds the line with `oscompat.windows_cmdline` (`-Key="value"`), and
  the script and args paths inside `-script=` / `-ExecCmds=` use 8.3 short names (`short_path`), since
  UE splits those values on spaces again ("Unreal Projects" has one).
- **Text files:** Python 3.12 on Windows reads and writes in the ANSI code page unless UTF-8 mode is
  on. The launcher and `ap.cmd` set `PYTHONUTF8=1`; `cli.main` re-runs itself with `-X utf8` when it
  isn't (ap.exe started some other way); CI sets it too.
- **Gated off natively:** TRELLIS (`s5_3d.run`), the vision model (`vlm.up`) and segmentation/depth.
  Doctor's `check_native_windows` turns their features off with `oscompat.NATIVE_WINDOWS_NOTE`; the UI
  follows Doctor (`data-needs`, ACTION_NEEDS). Runtimes without `command_windows` in
  installer/runtimes.toml are left out of the native wizard.
- **Discovery:** `asset_pipeline/win_tools.py` (Blender in Program Files/Steam/Store, Unreal from the
  Epic launcher's LauncherInstalled.dat, ComfyUI portable/desktop app/git checkout). `python -m
  asset_pipeline.win_tools comfy` prints what it finds.
- **Terrain:** `ue.build_plugin` uses RunUAT.bat (Win64) when vswhere finds Visual Studio with the C++
  tools, or prebuilt Win64 DLLs in the plugin folder; otherwise `PluginUnavailable`, and `ue_build`
  goes on with the static-mesh terrain and a warning line in the report.
- **File store:** read-only is shared by every NTFS hard link and blocks unlinking any of them, so
  objects stay writable on Windows (`filestore.READ_ONLY`), and `is_ref` is any hard-linked file.
- **Backups:** no rsync: `ue._copy_snapshot` copies in Python, hard-linking files whose sha256 matches
  the previous snapshot; `latest` is a text file when symlinks aren't allowed.
- **Restart (Reload bar):** no exec on Windows: `restart_process` starts a detached helper that runs
  the same `ap ui` after 3 s, then exits.
- **Tests:** `posix`-marked tests skip on Windows (conftest); CI deselects `comfy`, `blender`,
  `unreal` and `gpu`.
