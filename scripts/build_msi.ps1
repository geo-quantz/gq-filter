# build_msi.ps1
# PowerShell script to build the MSI installer for gqfilter.
# Requires WiX Toolset v3 (heat.exe, candle.exe, light.exe).

$ErrorActionPreference = "Stop"

Write-Host "--- Starting MSI Build for gqfilter ---" -ForegroundColor Cyan

# 1. Check for PyInstaller output directory
$distDir = "dist/gqfilter"
$exePath = Join-Path $distDir "gqfilter.exe"
if (!(Test-Path $exePath)) {
    Write-Error "Executable not found at $exePath. Please run scripts/build_windows.ps1 first."
}

# 2. Locate WiX Toolset v3
function Find-WixTool([string]$name) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    if ($env:WIX) {
        $candidate = Join-Path (Join-Path $env:WIX "bin") "$name.exe"
        if (Test-Path $candidate) { return $candidate }
    }
    $installs = Get-ChildItem "C:\Program Files (x86)\WiX Toolset v*" -ErrorAction SilentlyContinue
    foreach ($i in $installs) {
        $candidate = Join-Path (Join-Path $i.FullName "bin") "$name.exe"
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

$heat   = Find-WixTool "heat"
$candle = Find-WixTool "candle"
$light  = Find-WixTool "light"

if (-not ($heat -and $candle -and $light)) {
    Write-Error "WiX Toolset v3 not found (need heat.exe, candle.exe, light.exe). Install via 'choco install wixtoolset'."
}

Write-Host "Using heat:   $heat"
Write-Host "Using candle: $candle"
Write-Host "Using light:  $light"

# 3. Harvest dist/gqfilter into a fragment wxs
$harvestWxs = "packaging/gqfilter_files.wxs"
Write-Host "Harvesting $distDir -> $harvestWxs"
& $heat dir $distDir `
    -cg HarvestedComponents `
    -dr INSTALLFOLDER `
    -ag -scom -sreg -sfrag -srd `
    -var var.SourceDir `
    -out $harvestWxs
if ($LASTEXITCODE -ne 0) { Write-Error "heat.exe failed with exit $LASTEXITCODE" }

# 4. Compile both wxs files (x64 target to match 64-bit Program Files)
Write-Host "Compiling WiX sources..."
& $candle -nologo `
    -arch x64 `
    -dSourceDir="$distDir" `
    -out "packaging/" `
    "packaging/gqfilter.wxs" $harvestWxs
if ($LASTEXITCODE -ne 0) { Write-Error "candle.exe failed with exit $LASTEXITCODE" }

# 5. Link into MSI
Write-Host "Linking MSI..."
if (!(Test-Path "dist")) { New-Item -ItemType Directory -Path "dist" | Out-Null }
& $light -nologo `
    -b $distDir `
    -out "dist/gqfilter.msi" `
    "packaging/gqfilter.wixobj" "packaging/gqfilter_files.wixobj"
if ($LASTEXITCODE -ne 0) { Write-Error "light.exe failed with exit $LASTEXITCODE" }

Write-Host "MSI build successful: dist/gqfilter.msi" -ForegroundColor Green