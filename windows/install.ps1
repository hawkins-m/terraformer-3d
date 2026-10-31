<#
.SYNOPSIS
  Terraformer installer for Windows + NVIDIA, beta: WSL2 (the default, the full pipeline) or native
  Windows (the greybox path).

.DESCRIPTION
  It first asks which install to make (or take -Native / -Wsl):
    WSL2 (default)  the full pipeline inside Ubuntu on WSL2, steps 1-6 below.
    Native (beta)   no WSL: Layout, Greybox (Windows Blender), Shots, Frames through a Windows ComfyUI
                    and Build city in Unreal (UnrealEditor-Cmd.exe). TRELLIS and the vision model are
                    off there. It installs uv (astral.sh, per user, no admin), makes the app's Python 3.12
                    environment with it next to this checkout (.venv), writes the launcher and shortcuts,
                    then starts the setup wizard natively (python -m asset_pipeline.installer --profile
                    win-nvidia), which finds or installs ComfyUI (portable build), Blender and Unreal.
  The WSL2 install:
  1. Checks the Windows version (Windows 11, or Windows 10 21H2 / build 19044 or newer) and WSL.
  2. Installs WSL and Ubuntu if missing (asks first; installing WSL itself needs admin and a restart).
  3. Checks that nvidia-smi works inside WSL (the Windows NVIDIA driver provides it).
  4. Clones the repo inside WSL (default ~/terraformer-3d: the WSL filesystem, not C:\).
  5. Installs the launcher: %LOCALAPPDATA%\Terraformer\Terraformer.bat, a Start Menu shortcut and a
     desktop shortcut.
  6. Starts the setup wizard inside WSL with the NVIDIA profile (scripts/install.sh --profile wsl-nvidia)
     and opens it in the Windows default browser; the wizard does the rest.
  Beta: none of this has run on a Windows machine yet. Run it from install.bat.

.EXAMPLE
  install.bat
  install.bat -Native
  install.bat -Distro Ubuntu-24.04 -NoDesktopShortcut
#>
[CmdletBinding()]
param(
    [string]$Distro = "Ubuntu-24.04",
    [string]$Repo = "https://github.com/hawkins-m/terraformer-3d.git",
    [string]$Branch = "main",
    # inside WSL; single quotes keep $HOME for bash
    [string]$Checkout = '$HOME/terraformer-3d',
    [int]$Port = 8700,
    [switch]$Yes,
    [switch]$NoDesktopShortcut,
    [switch]$LauncherOnly,
    [switch]$Native,
    [switch]$Wsl
)
$ErrorActionPreference = "Continue"   # native tools report through exit codes, checked below
$env:WSL_UTF8 = "1"                 # wsl.exe prints UTF-8 instead of UTF-16 (newer WSL)
$Label = "Windows (WSL2) + NVIDIA, beta"

function Say([string]$m) { Write-Host $m }
function Ok([string]$m) { Write-Host "  OK  $m" -ForegroundColor Green }
function Fail([string]$m) { Write-Host "  ERROR  $m" -ForegroundColor Red; exit 1 }
function Ask([string]$q) {
    if ($Yes) { return $true }
    $a = Read-Host "$q [y/N]"
    return $a -match '^(y|yes)$'
}
function Text($out) { (($out | Out-String) -replace "`0", "").Trim() }
function InWsl([string]$cmd) {
    # run a bash command inside the distro on this console; the caller checks $LASTEXITCODE
    & wsl.exe -d $Distro -- bash -lc $cmd
}
function Write-Shortcuts([string]$bat, [string]$dest, [string]$what) {
    # the Terraformer icon (installer/assets/5a-green-wave.ico next to this script's folder), a Start Menu
    # shortcut and, unless -NoDesktopShortcut, a desktop one, all pointing at $bat
    $icon = "$env:SystemRoot\System32\shell32.dll,13"
    $ico = Join-Path (Split-Path $PSScriptRoot -Parent) "installer\assets\5a-green-wave.ico"
    if (Test-Path $ico) {
        Copy-Item -Force $ico (Join-Path $dest "terraformer.ico")
        $icon = (Join-Path $dest "terraformer.ico") + ",0"
    }
    $shell = New-Object -ComObject WScript.Shell
    $links = @(Join-Path ([Environment]::GetFolderPath("Programs")) "Terraformer.lnk")
    if (-not $NoDesktopShortcut) { $links += Join-Path ([Environment]::GetFolderPath("Desktop")) "Terraformer.lnk" }
    foreach ($l in $links) {
        $s = $shell.CreateShortcut($l)
        $s.TargetPath = $bat
        $s.WorkingDirectory = $dest
        $s.Description = "Terraformer ($what): opens the app in its own window (http://localhost:$Port/)"
        $s.IconLocation = $icon
        $s.Save()
        Ok "Shortcut: $l"
    }
}

Say "Terraformer installer: $Label"
Say "Nothing here has run on a Windows machine yet; please report what breaks."
Say ""

# --- 1. Windows version ------------------------------------------------------------------------
$build = [Environment]::OSVersion.Version.Build
if ($build -lt 19044) {
    Fail "Windows build $build is too old. GPU support in WSL2 needs Windows 11, or Windows 10 21H2 (build 19044) or newer."
}
Ok "Windows build $build"

# --- Which install: WSL2 (default) or native Windows (beta) -------------------------------------
if (-not $Native -and -not $Wsl -and -not $Yes -and -not $LauncherOnly) {
    Say ""
    Say "Which install?"
    Say "  1. WSL2 (recommended): the full pipeline, inside Ubuntu on WSL2."
    Say "  2. Native Windows (beta): the greybox path only (Layout, Greybox, Shots, Frames, Build city in"
    Say "     Unreal). No TRELLIS or vision model. No WSL needed."
    $pick = Read-Host "Choose 1 or 2 [1]"
    if ($pick -match '^\s*2\s*$') { $Native = $true }
}

function Find-Uv {
    $c = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in @((Join-Path $env:USERPROFILE ".local\bin\uv.exe"), (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"))) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Install-Native {
    $Label = "native Windows + NVIDIA, beta"
    Say ""
    Say "Native Windows (beta): the greybox path. TRELLIS and the vision model need the WSL install."
    $repoDir = Split-Path $PSScriptRoot -Parent
    if (-not (Test-Path (Join-Path $repoDir "pyproject.toml"))) {
        Fail "Run install.bat from inside the Terraformer folder (download the repo zip and extract it first)."
    }
    if ($repoDir -match ' ') {
        Say "  Note: the folder $repoDir has spaces in its path; if Unreal steps fail, move it to e.g. C:\terraformer-3d."
    }
    Ok "Terraformer folder: $repoDir"

    # NVIDIA driver (a warning only: Layout, Greybox and Unreal work without it; ComfyUI needs it)
    $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if ($smi) {
        $gpus = Text (& nvidia-smi.exe -L)
        Ok ("NVIDIA: " + (($gpus -replace '\(UUID[^)]*\)', '') -replace "`r?`n", '; '))
    } else {
        Say "  WARNING  nvidia-smi not found: install the NVIDIA driver (Game Ready or Studio) for ComfyUI."
    }

    # uv (Python and the app's environment)
    $uv = Find-Uv
    if (-not $uv) {
        if (-not (Ask "Install uv (astral.sh, a Python installer; per user, no admin rights) now?")) {
            Fail "The native install needs uv. Install it from https://docs.astral.sh/uv/ and run install.bat -Native again."
        }
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; irm https://astral.sh/uv/install.ps1 | iex"
        $uv = Find-Uv
        if (-not $uv) { Fail "uv did not install (see above)." }
    }
    Ok "uv: $uv"
    $venv = Join-Path $repoDir ".venv"
    $py = Join-Path $venv "Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Say "Creating the app's Python 3.12 environment in $venv (uv downloads Python if needed)"
        & $uv venv --allow-existing --python 3.12 $venv
        if ($LASTEXITCODE -ne 0) { Fail "uv venv failed." }
    }
    Say "Installing the Terraformer app into it"
    & $uv pip install --python $py -e $repoDir
    if ($LASTEXITCODE -ne 0) { Fail "Installing the app failed (see above)." }
    $ap = Join-Path $venv "Scripts\ap.exe"
    if (-not (Test-Path $ap)) { Fail "ap.exe is missing after the install ($ap)." }
    $env:PYTHONUTF8 = "1"
    & $ap config set paths.venv $venv | Out-Null
    Ok "App installed ($ap)"

    # launcher and shortcuts
    $dest = Join-Path $env:LOCALAPPDATA "Terraformer"
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    $bat = Join-Path $dest "Terraformer.bat"
    $template = Get-Content -Raw -Path (Join-Path $PSScriptRoot "Terraformer-native.bat")
    $template = $template -replace '(?m)^set "TF_AP=.*"', ('set "TF_AP=' + $ap.Replace('$', '$$') + '"')
    $template = $template -replace '(?m)^set "TF_PORT=.*"', "set `"TF_PORT=$Port`""
    [IO.File]::WriteAllText($bat, ($template -replace "`r?`n", "`r`n"), [Text.Encoding]::ASCII)
    Write-Shortcuts $bat $dest $Label

    # the setup wizard, natively: settings, ComfyUI (found or the portable build), Blender, Unreal, models
    Say ""
    Say "Starting the setup wizard (native Windows); it opens in your browser."
    Say "Keep this window open while it installs. Closing it pauses the install; run install.bat -Native again to resume."
    & $py -X utf8 -m asset_pipeline.installer --profile win-nvidia
    if ($LASTEXITCODE -ne 0) { Fail "The setup wizard stopped (see above). Fix it, then run install.bat -Native again: it resumes." }
    Say ""
    Say "Done. Start Terraformer from the Start Menu (or the desktop shortcut); it opens http://localhost:$Port/."
    Say "Check this machine any time with: $ap doctor"
    Say "To test it, follow docs\windows-native-test.md."
    exit 0
}

if ($Native) { Install-Native }

# --- 2. WSL and Ubuntu -------------------------------------------------------------------------
if (-not $LauncherOnly) {
    $hasWsl = $false
    if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
        $null = & wsl.exe --status 2>&1
        $hasWsl = ($LASTEXITCODE -eq 0)
    }
    if (-not $hasWsl) {
        Say "WSL is not installed."
        if (-not (Ask "Install WSL now? (needs administrator rights, then a restart)")) { Fail "WSL is needed. Install it with: wsl --install" }
        Start-Process wsl.exe -ArgumentList "--install", "--no-distribution" -Verb RunAs -Wait
        Say "WSL is installed. Restart Windows, then run install.bat again."
        exit 0
    }
    $null = & wsl.exe --set-default-version 2 2>&1
    Ok "WSL"

    $distros = (Text (& wsl.exe --list --quiet)) -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    if ($distros -notcontains $Distro) {
        $found = if ($distros) { $distros -join ', ' } else { 'none' }
        Say "$Distro is not installed in WSL (found: $found)."
        if (-not (Ask "Install $Distro now? It asks you to create a Linux user name and password.")) {
            Fail "Terraformer needs an Ubuntu in WSL. Install one with: wsl --install -d $Distro (or pass -Distro NAME)."
        }
        Say ""
        Say 'Ubuntu will open and ask you to create a Linux username and password. When you see a Linux prompt (ending in $), type exit and press Enter to return to this installer.'
        Say ""
        & wsl.exe --install -d $Distro
        if ($LASTEXITCODE -ne 0) { Fail "wsl --install -d $Distro failed (exit $LASTEXITCODE)." }
        Read-Host "When Ubuntu has finished setting up your Linux user, press Enter to continue"
    }
    Ok "$Distro"

    $kernel = Text (& wsl.exe -d $Distro -- uname -r)
    if ($kernel -notmatch "WSL2") {
        Say "$Distro runs under WSL 1 (kernel $kernel): the GPU needs WSL 2."
        if (-not (Ask "Convert $Distro to WSL 2 now?")) { Fail "Convert it with: wsl --set-version $Distro 2" }
        & wsl.exe --set-version $Distro 2
        if ($LASTEXITCODE -ne 0) { Fail "wsl --set-version $Distro 2 failed." }
    }
    Ok "WSL 2 ($kernel)"

    # --- 3. NVIDIA inside WSL ------------------------------------------------------------------
    $gpus = Text (& wsl.exe -d $Distro -- bash -lc "nvidia-smi -L 2>/dev/null || /usr/lib/wsl/lib/nvidia-smi -L")
    if ($LASTEXITCODE -ne 0 -or $gpus -notmatch "GPU") {
        Fail ("nvidia-smi doesn't work inside WSL. Install or update the NVIDIA driver for Windows (Game Ready or " +
              "Studio); it brings the GPU into WSL. Don't install a Linux NVIDIA driver inside Ubuntu. Then run " +
              "install.bat again.")
    }
    $gpuList = ($gpus -replace '\(UUID[^)]*\)', '') -replace "`r?`n", '; '
    Ok "NVIDIA in WSL: $gpuList"

    # --- 4. The repo inside WSL and the normal Linux install -----------------------------------
    InWsl "test -d $Checkout/.git"
    if ($LASTEXITCODE -ne 0) {
        InWsl "command -v git >/dev/null"
        if ($LASTEXITCODE -ne 0) { Fail "git is missing in $Distro. In Ubuntu: sudo apt-get install -y git" }
        Say "Cloning $Repo into $Checkout (inside WSL)"
        InWsl "git clone --branch $Branch $Repo $Checkout"
        if ($LASTEXITCODE -ne 0) { Fail "git clone failed." }
    } else {
        Say "Using the existing checkout at $Checkout"
        if (Ask "Update it (git pull --ff-only)?") {
            InWsl "git -C $Checkout pull --ff-only"
            if ($LASTEXITCODE -ne 0) { Fail "git pull failed; update it by hand in WSL." }
        }
    }
}

# --- 5. Launcher and shortcuts -----------------------------------------------------------------
$dest = Join-Path $env:LOCALAPPDATA "Terraformer"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$bat = Join-Path $dest "Terraformer.bat"
$template = Get-Content -Raw -Path (Join-Path $PSScriptRoot "Terraformer.bat")
$template = $template -replace '(?m)^set "TF_DISTRO=.*"', "set `"TF_DISTRO=$Distro`""
$template = $template -replace '(?m)^set "TF_REPO=.*"', ('set "TF_REPO=' + $Checkout.Replace('$', '$$') + '"')
$template = $template -replace '(?m)^set "TF_PORT=.*"', "set `"TF_PORT=$Port`""
[IO.File]::WriteAllText($bat, ($template -replace "`r?`n", "`r`n"), [Text.Encoding]::ASCII)

Write-Shortcuts $bat $dest $Label

# --- 6. The setup wizard ------------------------------------------------------------------------
if (-not $LauncherOnly) {
    Say ""
    Say "Starting the setup wizard inside WSL (scripts/install.sh --profile wsl-nvidia); it opens in your browser."
    Say "Keep this window open while it installs. Closing it pauses the install; run install.bat again to resume."
    InWsl "cd $Checkout && bash scripts/install.sh --profile wsl-nvidia"
    if ($LASTEXITCODE -ne 0) {
        Fail "The setup wizard stopped (see above). Fix it, then run install.bat again: it resumes."
    }
}

Say ""
Say "Done. Start Terraformer from the Start Menu (or the desktop shortcut). It opens http://localhost:$Port/"
Say "in your browser; closing its window stops it. Change components or settings later with: ap setup (in WSL)."
