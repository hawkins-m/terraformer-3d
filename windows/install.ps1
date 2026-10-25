<#
.SYNOPSIS
  Terraformer installer for Windows (WSL2) + NVIDIA, beta.

.DESCRIPTION
  1. Checks the Windows version (Windows 11, or Windows 10 21H2 / build 19044 or newer) and WSL.
  2. Installs WSL and Ubuntu if missing (asks first; installing WSL itself needs admin and a restart).
  3. Checks that nvidia-smi works inside WSL (the Windows NVIDIA driver provides it).
  4. Clones the repo inside WSL (default ~/terraformer-3d: the WSL filesystem, not C:\) and runs the
     normal Linux install there with the NVIDIA profile: scripts/install.sh --profile wsl-nvidia.
  5. Installs the launcher: %LOCALAPPDATA%\Terraformer\Terraformer.bat, a Start Menu shortcut and a
     desktop shortcut.
  Beta: none of this has run on a Windows machine yet. Run it from install.bat.

.EXAMPLE
  install.bat
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
    [switch]$LauncherOnly
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

Say "Terraformer installer: $Label"
Say "Nothing here has run on a Windows machine yet; please report what breaks."
Say ""

# --- 1. Windows version ------------------------------------------------------------------------
$build = [Environment]::OSVersion.Version.Build
if ($build -lt 19044) {
    Fail "Windows build $build is too old. GPU support in WSL2 needs Windows 11, or Windows 10 21H2 (build 19044) or newer."
}
Ok "Windows build $build"

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
        Say "$Distro is not installed in WSL (found: $(if ($distros) { $distros -join ', ' } else { 'none' }))."
        if (-not (Ask "Install $Distro now? It asks you to create a Linux user name and password.")) {
            Fail "Terraformer needs an Ubuntu in WSL. Install one with: wsl --install -d $Distro (or pass -Distro NAME)."
        }
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
    Ok "NVIDIA in WSL: $($gpus -replace '\(UUID[^)]*\)', '' -replace "`r?`n", '; ')"

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
    $flags = "--profile wsl-nvidia" + $(if ($Yes) { " --yes" } else { "" })
    Say "Running the Linux install inside WSL: scripts/install.sh $flags"
    InWsl "cd $Checkout && bash scripts/install.sh $flags"
    if ($LASTEXITCODE -ne 0) {
        Fail "The Linux install stopped (see above). Fix it, then run install.bat again."
    }
    Ok "Terraformer installed in WSL"
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

$shell = New-Object -ComObject WScript.Shell
$links = @(Join-Path ([Environment]::GetFolderPath("Programs")) "Terraformer.lnk")
if (-not $NoDesktopShortcut) { $links += Join-Path ([Environment]::GetFolderPath("Desktop")) "Terraformer.lnk" }
foreach ($l in $links) {
    $s = $shell.CreateShortcut($l)
    $s.TargetPath = $bat
    $s.WorkingDirectory = $dest
    $s.Description = "Terraformer ($Label): opens the app at http://localhost:$Port/"
    $s.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
    $s.Save()
    Ok "Shortcut: $l"
}

Say ""
Say "Done. Start Terraformer from the Start Menu (or the desktop shortcut). It opens http://localhost:$Port/"
Say "in your browser; closing its window stops it."
Say "Still to do inside WSL (README, Windows section): ComfyUI, ap models download --required, the model runtimes, ap doctor."
