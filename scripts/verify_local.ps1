param(
    [switch]$Install,
    [switch]$UpgradePip,
    [switch]$Smoke
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$WebDir = Join-Path $Root "web"
$Port = 8010

function Write-Stage {
    param([Parameter(Mandatory = $true)][string]$Name)
    Write-Host ""
    Write-Host "==> $Name"
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

function Test-TcpPort {
    param([Parameter(Mandatory = $true)][int]$Port)

    $Client = [System.Net.Sockets.TcpClient]::new()
    try {
        $AsyncResult = $Client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $AsyncResult.AsyncWaitHandle.WaitOne(250)) {
            return $false
        }
        $Client.EndConnect($AsyncResult)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $Client.Close()
    }
}

function Test-FrontendTools {
    $Tsc = Join-Path $WebDir "node_modules\.bin\tsc.cmd"
    $Vite = Join-Path $WebDir "node_modules\.bin\vite.cmd"
    return (Test-Path -LiteralPath $Tsc) -and (Test-Path -LiteralPath $Vite)
}

function Write-NpmPermissionAdvice {
    Write-Host ""
    Write-Host "npm failed with a Windows permission error. This is usually caused by locked files, antivirus scanning, or npm cache permissions."
    Write-Host "Suggested recovery:"
    Write-Host "- Delete web/node_modules"
    Write-Host "- Clean the npm cache"
    Write-Host "- Disable antivirus real-time scanning temporarily or add the project directory to its allowlist"
    Write-Host "- Run npm install again"
}

Set-Location $Root

Write-Stage "Check virtualenv"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing .venv Python at $Python. Create it with: py -3.12 -m venv .venv"
}

$Version = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read .venv Python version."
}
if ($Version -notmatch "^3\.12\.") {
    throw ".venv Python must be 3.12.x, found $Version"
}
Write-Host ".venv Python $Version"

if ($UpgradePip) {
    Write-Stage "Upgrade Python packaging tools"
    Invoke-Native $Python @("-m", "pip", "install", "-U", "pip", "setuptools", "wheel")
}

if ($Install) {
    Write-Stage "Install Python dependencies"
    Invoke-Native $Python @("-m", "pip", "install", "-r", "requirements.txt")

    if (Test-Path -LiteralPath (Join-Path $Root "requirements-dev.txt")) {
        Write-Stage "Install Python development dependencies"
        Invoke-Native $Python @("-m", "pip", "install", "-r", "requirements-dev.txt")
    }

    Write-Stage "Install frontend dependencies"
    Push-Location $WebDir
    try {
        try {
            if (Test-Path -LiteralPath (Join-Path $WebDir "package-lock.json")) {
                Invoke-Native "npm" @("ci")
            }
            else {
                Invoke-Native "npm" @("install")
            }
        }
        catch {
            if ($_.Exception.Message -match "EPERM|-4048|permission") {
                Write-NpmPermissionAdvice
            }
            throw
        }
    }
    finally {
        Pop-Location
    }

    if (-not (Test-FrontendTools)) {
        throw "Frontend dependencies are incomplete after install. Missing node_modules\.bin\tsc.cmd or node_modules\.bin\vite.cmd."
    }
}

Write-Stage "Compile Python sources"
$PycachePrefix = Join-Path $env:TEMP "ming_verify_pycache"
New-Item -ItemType Directory -Force -Path $PycachePrefix | Out-Null
$env:PYTHONPYCACHEPREFIX = $PycachePrefix
$CompileExclude = "(^|[\\/])(\.git|\.venv|\.pytest_cache|__pycache__|node_modules|data|logs|scripts[\\/]runs|web[\\/]node_modules|web[\\/]dist)([\\/]|$)|(^|[\\/])tmp[^\\/]*([\\/]|$)"
Invoke-Native $Python @(
    "-m", "compileall", "-q", "-x", $CompileExclude,
    "ming_sim", "server", "tests", "scripts",
    "web_app.py", "server_backend.py", "main.py", "launcher.py"
)

Write-Stage "Run Python tests"
Invoke-Native $Python @("-m", "pytest", "-q")

Write-Stage "Build frontend"
if (Test-FrontendTools) {
    Push-Location $WebDir
    try {
        Invoke-Native "npm" @("run", "build")
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Host "Frontend dependencies are incomplete. Run scripts\verify_local.ps1 -Install"
    Write-Host "Skipping frontend build."
}

if ($Smoke) {
    Write-Stage "Smoke test FastAPI backend"
    if (Test-TcpPort $Port) {
        throw "Port $Port is already in use."
    }

    $Stdout = Join-Path $env:TEMP "ming_verify_uvicorn_stdout.log"
    $Stderr = Join-Path $env:TEMP "ming_verify_uvicorn_stderr.log"
    Remove-Item -LiteralPath $Stdout, $Stderr -ErrorAction SilentlyContinue

    $Process = Start-Process `
        -FilePath $Python `
        -ArgumentList @("-m", "uvicorn", "web_app:app", "--host", "127.0.0.1", "--port", "$Port") `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $Stdout `
        -RedirectStandardError $Stderr `
        -WindowStyle Hidden `
        -PassThru

    try {
        $Ready = $false
        for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
            if ($Process.HasExited) {
                break
            }
            if (Test-TcpPort $Port) {
                $Ready = $true
                break
            }
            Start-Sleep -Milliseconds 500
        }

        if (-not $Ready) {
            $Err = if (Test-Path -LiteralPath $Stderr) { Get-Content -Raw $Stderr } else { "" }
            throw "Backend did not start on 127.0.0.1:$Port. $Err"
        }

        Write-Host "Backend listening on 127.0.0.1:$Port"
    }
    finally {
        if ($null -ne $Process -and -not $Process.HasExited) {
            Stop-Process -Id $Process.Id -ErrorAction SilentlyContinue
            Wait-Process -Id $Process.Id -Timeout 5 -ErrorAction SilentlyContinue
        }
    }
}

Write-Host ""
Write-Host "Local verification completed."
