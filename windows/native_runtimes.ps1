<#
.SYNOPSIS
  Runtimes for Terraformer on native Windows (beta): the ComfyUI portable build and comfy-api-proxy.

.DESCRIPTION
  -Action comfyui -Target DIR
      Installs the ComfyUI portable build for NVIDIA so that DIR is its ComfyUI folder
      (DIR = <root>\ComfyUI_windows_portable\ComfyUI). Skips the download when DIR\main.py exists.
      The .7z is unpacked with Windows' tar.exe, or 7-Zip when tar can't read it.
  -Action proxy
      comfy-api-proxy (for the sdk ComfyUI backend) in its own venv under $env:AP_ROOT\envs, with uv.
  The setup wizard runs this (installer/runtimes.toml command_windows); install.ps1 too.
  Works on Windows PowerShell 5.1.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet("comfyui", "proxy")][string]$Action,
    [string]$Target = "",
    [string]$ProxyVersion = "0.1.6",
    [string]$ComfyUrl = "https://github.com/comfyanonymous/ComfyUI/releases/latest/download/ComfyUI_windows_portable_nvidia.7z"
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"     # Invoke-WebRequest is many times slower with the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Say([string]$m) { Write-Host $m }
function Fail([string]$m) { Write-Host "ERROR: $m"; exit 1 }

function Find-Uv {
    $c = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in @((Join-Path $env:USERPROFILE ".local\bin\uv.exe"), (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"))) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Storage {
    if ($env:AP_ROOT) { return $env:AP_ROOT }
    return (Join-Path $env:LOCALAPPDATA "terraformer")
}

if ($Action -eq "proxy") {
    $uv = Find-Uv
    if (-not $uv) { Fail "uv is not installed (install.bat installs it)." }
    $envDir = Join-Path (Storage) "envs\comfy-api-proxy"
    $exe = Join-Path $envDir "Scripts\comfy-api-proxy.exe"
    if (-not (Test-Path (Join-Path $envDir "Scripts\python.exe"))) {
        & $uv venv --allow-existing --python 3.12 $envDir
        if ($LASTEXITCODE -ne 0) { Fail "uv venv $envDir failed." }
    }
    & $uv pip install --python (Join-Path $envDir "Scripts\python.exe") "comfy-api-proxy==$ProxyVersion"
    if ($LASTEXITCODE -ne 0) { Fail "installing comfy-api-proxy $ProxyVersion failed." }
    if (-not (Test-Path $exe)) { Fail "comfy-api-proxy.exe is missing after the install ($exe)." }
    Say "installed comfy-api-proxy $ProxyVersion in $envDir"
    exit 0
}

# --- comfyui ----------------------------------------------------------------------------------
if (-not $Target) { Fail "give -Target: the ComfyUI folder inside the portable build." }
$Target = [Environment]::ExpandEnvironmentVariables($Target).Replace("/", "\")
if (Test-Path (Join-Path $Target "main.py")) {
    Say "ComfyUI is already in $Target"
    exit 0
}
if ((Split-Path $Target -Leaf) -ne "ComfyUI") { Fail "-Target must end in ComfyUI_windows_portable\ComfyUI (got $Target)." }
$root = Split-Path $Target -Parent                       # ...\ComfyUI_windows_portable
$parent = Split-Path $root -Parent
New-Item -ItemType Directory -Force -Path $parent | Out-Null
$archive = Join-Path $parent "ComfyUI_windows_portable_nvidia.7z"
if (-not (Test-Path $archive)) {
    Say "Downloading the ComfyUI portable build (about 2 GB): $ComfyUrl"
    Invoke-WebRequest -Uri $ComfyUrl -OutFile "$archive.part" -UseBasicParsing
    Move-Item -Force "$archive.part" $archive
}
Say "Unpacking $archive"
$unpacked = $false
$tar = Join-Path $env:SystemRoot "System32\tar.exe"
if (Test-Path $tar) {
    & $tar -xf $archive -C $parent
    $unpacked = ($LASTEXITCODE -eq 0) -and (Test-Path (Join-Path $root "ComfyUI\main.py"))
}
if (-not $unpacked) {
    $sevenZip = @((Join-Path $env:ProgramFiles "7-Zip\7z.exe"), (Join-Path ${env:ProgramFiles(x86)} "7-Zip\7z.exe")) |
        Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $sevenZip) {
        Fail ("Windows' tar can't unpack $archive on this Windows version. Install 7-Zip (7-zip.org) and run this " +
              "again, or unpack it yourself so that $Target\main.py exists.")
    }
    & $sevenZip x $archive "-o$parent" -y | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "7-Zip couldn't unpack $archive." }
}
if (-not (Test-Path (Join-Path $Target "main.py"))) {
    Fail "the archive didn't unpack into $root as expected; unpack it so that $Target\main.py exists."
}
Remove-Item -Force $archive
Say "ComfyUI portable installed in $root"
exit 0
