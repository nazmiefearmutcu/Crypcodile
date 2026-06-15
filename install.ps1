# Display ASCII banner
$banner = @'
  ____                               _ _ _ 
 / ___|_ __ _   _ _ __   ___ ___  __| (_) | ___ 
| |   | '__| | | | '_ \ / __/ _ \/ _` | | |/ _ \
| |___| |  | |_| | |_) | (_| (_) | (_| | | |  __/
 \____|_|   \__, | .__/ \___\___/\__,_|_|_|\___|
            |___/|_|                            
'@

Write-Host $banner -ForegroundColor Cyan

Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "      Crypcodile CLI Framework Installer         " -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Verify python is installed and checks that the version is >= 3.12
Write-Host "Verifying Python installation..."

$pythonCmd = $null

# Check common python commands on Windows: py, python, python3
if (Get-Command "py" -ErrorAction SilentlyContinue) {
    # Verify py can run a python 3 version and is >= 3.12
    $versionCheck = & py -c "import sys; print(sys.version_info >= (3, 12))" 2>$null
    if ($versionCheck -eq "True") {
        $pythonCmd = "py"
    }
}

if (-not $pythonCmd -and (Get-Command "python" -ErrorAction SilentlyContinue)) {
    $versionCheck = & python -c "import sys; print(sys.version_info >= (3, 12))" 2>$null
    if ($versionCheck -eq "True") {
        $pythonCmd = "python"
    }
}

if (-not $pythonCmd -and (Get-Command "python3" -ErrorAction SilentlyContinue)) {
    $versionCheck = & python3 -c "import sys; print(sys.version_info >= (3, 12))" 2>$null
    if ($versionCheck -eq "True") {
        $pythonCmd = "python3"
    }
}

if (-not $pythonCmd) {
    # Check if python exists but is an older version
    $foundPython = $null
    if (Get-Command "python" -ErrorAction SilentlyContinue) { $foundPython = "python" }
    elseif (Get-Command "python3" -ErrorAction SilentlyContinue) { $foundPython = "python3" }
    elseif (Get-Command "py" -ErrorAction SilentlyContinue) { $foundPython = "py" }

    Write-Host "Error: Python 3.12+ was not found on your system." -ForegroundColor Red
    if ($foundPython) {
        $currentVer = & $foundPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
        Write-Host "Found Python version: $currentVer (Minimum required: 3.12)" -ForegroundColor Yellow
    } else {
        Write-Host "Python is not installed." -ForegroundColor Yellow
    }
    
    Write-Host ""
    Write-Host "How to install Python 3.12+ on Windows:" -ForegroundColor White
    Write-Host "----------------------------------------"
    Write-Host "Option 1: Download from python.org"
    Write-Host "  Go to https://www.python.org/downloads/ and download Python 3.12+"
    Write-Host "  Ensure you check 'Add python.exe to PATH' during installation."
    Write-Host ""
    Write-Host "Option 2: Install via Winget (Windows Package Manager)"
    Write-Host "  winget install Python.Python.3.12"
    Write-Host "----------------------------------------"
    exit 1
}

$currentVer = & $pythonCmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
Write-Host "Python version verified: $currentVer ($pythonCmd)" -ForegroundColor Green

# Verify Git is installed
if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) {
    Write-Host "Error: 'git' is not installed, which is required to download the package from GitHub." -ForegroundColor Red
    Write-Host "Please install git first. For example, using winget:" -ForegroundColor Yellow
    Write-Host "  winget install Git.Git"
    exit 1
}
$gitVersion = (git --version) | Select-Object -First 1
Write-Host "Git version verified: $gitVersion" -ForegroundColor Green

# 2. Creates directory $env:USERPROFILE\.crypcodile
$crypcodileDir = Join-Path $env:USERPROFILE ".crypcodile"
Write-Host "Creating directory $crypcodileDir..."
if (-not (Test-Path $crypcodileDir)) {
    New-Item -ItemType Directory -Path $crypcodileDir | Out-Null
}

# 3. Creates virtual environment $env:USERPROFILE\.crypcodile\venv
$venvDir = Join-Path $crypcodileDir "venv"
Write-Host "Creating virtual environment at $venvDir..."
& $pythonCmd -m venv $venvDir
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Failed to create virtual environment." -ForegroundColor Red
    exit 1
}

# 4. Upgrades pip and installs the CLI package
$pipExe = Join-Path $venvDir "Scripts\pip.exe"
Write-Host "Upgrading pip inside virtual environment..."
& $pipExe install --upgrade pip | Out-Null

Write-Host "Installing Crypcodile from Git repository..."
& $pipExe install "git+https://github.com/nazmiefearmutcu/Crypcodile.git"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Package installation failed." -ForegroundColor Red
    exit 1
}

# 5. Adds $env:USERPROFILE\.crypcodile\venv\Scripts to the User PATH environment variable
Write-Host "Updating User PATH environment variable..."
$userPathTarget = [System.EnvironmentVariableTarget]::User
$currentPath = [System.Environment]::GetEnvironmentVariable("Path", $userPathTarget)
$targetScriptsPath = Join-Path $venvDir "Scripts"

$alreadyExists = $false
if ($currentPath) {
    $paths = $currentPath -split ';'
    foreach ($p in $paths) {
        if ($p.Trim().TrimEnd('\') -eq $targetScriptsPath.Trim().TrimEnd('\')) {
            $alreadyExists = $true
            break
        }
    }
}

if (-not $alreadyExists) {
    # Extra safety check to avoid partial match false negatives/positives
    if ($currentPath -notlike "*\.crypcodile\venv\Scripts*") {
        $newPath = $currentPath
        if ($currentPath -and -not $currentPath.EndsWith(';')) {
            $newPath += ";"
        }
        $newPath += $targetScriptsPath
        [System.Environment]::SetEnvironmentVariable("Path", $newPath, $userPathTarget)
        Write-Host "Added $targetScriptsPath to the User PATH environment variable." -ForegroundColor Green
    } else {
        Write-Host "$targetScriptsPath is already present in User PATH." -ForegroundColor Yellow
    }
} else {
    Write-Host "$targetScriptsPath is already present in User PATH." -ForegroundColor Yellow
}

# 6. Displays prominent notification message
Write-Host ""
Write-Host "========================================================================" -ForegroundColor Green
Write-Host "Crypcodile has successfully downloaded!" -ForegroundColor Green
Write-Host "Please restart your terminal/PowerShell session before using it." -ForegroundColor White
Write-Host "========================================================================" -ForegroundColor Green
Write-Host ""
