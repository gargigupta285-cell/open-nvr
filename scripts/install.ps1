# OpenNVR interactive installer for Windows (also detects PowerShell on Linux/macOS).
# Mode: 'install' (fresh; fill missing, keep existing) or 'reconfigure'
# (re-prompt values with the current value as default).
param([string]$Mode = 'install')
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BaseCompose = 'docker-compose.yml'
Set-Location $ProjectRoot

function Info([string]$Message) { Write-Host "  $Message" }
function Ok([string]$Message) { Write-Host "  ✓ $Message" -ForegroundColor Green }
function Warn([string]$Message) { Write-Host "  ⚠ $Message" -ForegroundColor Yellow }
function Fail([string]$Message) { Write-Host "  X $Message" -ForegroundColor Red; exit 1 }
function Show-Logo {
    $c = 'Cyan'
    Write-Host ''
    Write-Host '   ___                   _   ___     ______ ' -ForegroundColor $c
    Write-Host '  / _ \ _ __   ___ _ __ | \ | \ \   / /  _ \' -ForegroundColor $c
    Write-Host " | | | | '_ \ / _ \ '_ \|  \| |\ \ / /| |_) |" -ForegroundColor $c
    Write-Host ' | |_| | |_) |  __/ | | | |\  | \ V / |  _ < ' -ForegroundColor $c
    Write-Host '  \___/| .__/ \___|_| |_|_| \_|  \_/  |_| \_\' -ForegroundColor $c
    Write-Host '       |_|                                   ' -ForegroundColor $c
    Write-Host '  Self-hosted NVR — the cameras are yours.' -ForegroundColor DarkGray
    Write-Host ''
}
function Ask-YesNo([string]$Prompt, [bool]$Default = $false) {
    $hint = if ($Default) { 'Y/n' } else { 'y/N' }
    $answer = Read-Host "  $Prompt [$hint]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer -match '^[Yy]'
}
function Ask-Value([string]$Prompt, [string]$Default) {
    $answer = Read-Host "  $Prompt [$Default]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer
}
function Ask-Secret([string]$Prompt) {
    $secure = Read-Host "  $Prompt" -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}
function Explain([string]$What, [string]$Required, [string]$Default, [string]$Where = '') {
    Write-Host "  $What"
    Write-Host ("    required: {0,-4}  default: {1}" -f $Required, $Default)
    if ($Where) { Write-Host "    note: $Where" }
}
# Curated, ALWAYS-prompted value with an explanation. Enter keeps the current
# .env value (or the given default on a fresh install); typing overrides it.
function Configure-Value([string]$Key, [string]$Label, [string]$Default, [string]$What, [string]$Required, [string]$Where = '') {
    $current = Get-EnvValue $Key
    if (-not [string]::IsNullOrWhiteSpace($current)) { $Default = $current }
    Write-Host ''
    Explain $What $Required $Default $Where
    Set-EnvValue $Key (Ask-Value $Label $Default)
}

function Detect-Platform {
    if ($IsLinux) { $script:Platform = 'Linux'; $script:DefaultRecordings = '/var/lib/opennvr/recordings' }
    elseif ($IsMacOS) { $script:Platform = 'macOS'; $script:DefaultRecordings = '/Users/Shared/opennvr-recordings' }
    else { $script:Platform = 'Windows'; $script:DefaultRecordings = 'C:/opennvr/recordings' }
    Ok "Detected $script:Platform (Docker bridge mode)"
}
function Check-Prerequisites {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Fail 'Docker is not installed. Install Docker Desktop, then re-run.'
    }
    # Native commands (docker) write to stderr when the daemon is down. Under
    # $ErrorActionPreference='Stop' that stderr becomes a NativeCommandError
    # that prints an ugly stack trace and aborts before our friendly message.
    # Silence the policy + stderr around the probes and judge by exit code only.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    docker compose version 2>$null | Out-Null; $composeOk = ($LASTEXITCODE -eq 0)
    docker info 2>$null | Out-Null;            $dockerOk  = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEAP
    if (-not $composeOk) { Fail 'Docker Compose v2 is required. Update Docker Desktop and re-run.' }
    if (-not $dockerOk)  { Fail 'Docker is not running. Start Docker Desktop, wait until it is ready, then re-run.' }
    if (-not (Test-Path $BaseCompose)) { Fail "$BaseCompose was not found in $ProjectRoot" }
}

# CRITICAL: read AND write .env with the SAME explicit encoding (UTF-8, no BOM).
# Windows PowerShell 5.1's Get-Content defaults to ANSI (Windows-1252) while we
# write UTF-8 — so any non-ASCII byte in the file (an em-dash in a comment, an
# accented value) is re-decoded wrong and re-encoded larger on every rewrite.
# Across the ~13 Set-EnvValue calls per install that compounds into a multi-MB
# .env of mojibake. Pinning both sides to UTF-8 makes the round trip byte-stable.
$script:Utf8NoBom = New-Object Text.UTF8Encoding($false)
function Read-EnvLines {
    $p = Join-Path $ProjectRoot '.env'
    if (-not (Test-Path $p)) { return @() }
    return [IO.File]::ReadAllLines($p, $script:Utf8NoBom)
}
function Get-EnvValue([string]$Key) {
    $line = Read-EnvLines | Where-Object { $_ -match ('^' + [regex]::Escape($Key) + '=') } | Select-Object -Last 1
    if (-not $line) { return '' }
    $value = ($line -split '=', 2)[1] -replace '\s+#.*$', ''
    return $value.Trim().Trim('"').Trim("'")
}
function Set-EnvValue([string]$Key, [string]$Value) {
    $lines = [Collections.Generic.List[string]](Read-EnvLines)
    $pattern = '^' + [regex]::Escape($Key) + '='
    $output = [Collections.Generic.List[string]]::new(); $written = $false
    foreach ($line in $lines) {
        if ($line -match $pattern) {
            if (-not $written) { $output.Add("$Key=$Value"); $written = $true }
        } else { $output.Add($line) }
    }
    if (-not $written) { $output.Add(''); $output.Add("$Key=$Value") }
    [IO.File]::WriteAllLines((Join-Path $ProjectRoot '.env'), $output, $script:Utf8NoBom)
}
function Test-MissingOrPlaceholder([string]$Value) {
    return [string]::IsNullOrWhiteSpace($Value) -or $Value -match '^(dev_|insecure_|change_me|your_|changeme|placeholder|dummy|CKLghtP4rWz8J9vN2xQ5mT7yU8kF6bD3eH1aG4cS0wE=)'
}
function New-RandomBytes([int]$Count) { $b = New-Object byte[] $Count; $rng = [Security.Cryptography.RandomNumberGenerator]::Create(); try { $rng.GetBytes($b) } finally { $rng.Dispose() }; return $b }
function New-Hex([int]$Bytes) { return ((New-RandomBytes $Bytes) | ForEach-Object { $_.ToString('x2') }) -join '' }
function New-Password { return [Convert]::ToBase64String((New-RandomBytes 36)).Replace('+','').Replace('/','').Replace('=','').Substring(0,32) }
function New-FernetKey { return [Convert]::ToBase64String((New-RandomBytes 32)).Replace('+','-').Replace('/','_') }
function Ensure-PlainValue([string]$Key, [string]$Label, [string]$Default) {
    $current = Get-EnvValue $Key
    if (-not [string]::IsNullOrWhiteSpace($current)) {
        # Fresh install: keep existing, don't nag. Reconfigure: offer current as default.
        if ($script:Mode -ne 'reconfigure') { return }
        $Default = $current
    }
    Set-EnvValue $Key (Ask-Value $Label $Default)
}
function Ensure-SecretValue([string]$Key, [string]$Label, [string]$Generated) {
    $current = Get-EnvValue $Key
    if (-not (Test-MissingOrPlaceholder $current)) { Ok "$Label already configured"; return }
    if (Ask-YesNo "$Label is missing or insecure. Use a newly generated value?" $true) { $value = $Generated }
    else { $value = Ask-Secret "Enter $Label"; if ([string]::IsNullOrWhiteSpace($value)) { Fail "$Label cannot be empty" } }
    Set-EnvValue $Key $value; Ok "$Label configured"
}
function Prepare-Environment {
    if (-not (Test-Path '.env')) {
        if (-not (Test-Path '.env.example')) { Fail '.env.example is missing' }
        Copy-Item '.env.example' '.env'; Ok 'Created .env from .env.example'
    } else { Ok 'Using existing .env; secrets are preserved, and you can update values below' }

    # Secrets — generated automatically; prompted only if still a placeholder.
    Ensure-SecretValue POSTGRES_PASSWORD 'PostgreSQL password' (New-Password)
    Ensure-SecretValue SECRET_KEY 'JWT signing key' (New-Hex 32)
    Ensure-SecretValue CREDENTIAL_ENCRYPTION_KEY 'credential encryption key' (New-FernetKey)
    Ensure-SecretValue INTERNAL_API_KEY 'internal API key' (New-Password)
    Ensure-SecretValue MEDIAMTX_SECRET 'MediaMTX webhook secret' (New-Hex 32)

    # Rarely-changed identifiers — filled only if missing.
    Ensure-PlainValue POSTGRES_USER 'PostgreSQL user' 'opennvr_user'
    Ensure-PlainValue POSTGRES_DB 'PostgreSQL database' 'opennvr_db'

    # Curated, explained settings. Enter keeps the [default]; all local.
    Write-Host ''
    Write-Host '  -- Basic settings -------------------------------------'
    Configure-Value DEFAULT_ADMIN_USERNAME 'Administrator username' 'admin' `
        'Login name for the first OpenNVR admin account.' 'yes' `
        'You pick this yourself - no external account involved.'
    Configure-Value DEFAULT_ADMIN_EMAIL 'Administrator email' 'admin@opennvr.local' `
        'Contact email tied to the admin account.' 'yes' `
        'Any address works; the placeholder is fine for an offline setup.'
    Configure-Value RECORDINGS_PATH 'Recordings folder on this machine' $script:DefaultRecordings `
        'Host directory where recorded video segments are written.' 'yes' `
        'Created automatically if it does not exist yet.'

    $recordings = Get-EnvValue RECORDINGS_PATH
    if (-not (Test-Path $recordings)) { try { New-Item -ItemType Directory -Force -Path $recordings | Out-Null } catch { Warn 'Could not create recordings directory; Docker will try' } }
}

function Find-ExampleCompose([string]$Name) {
    $candidates = @("docker-compose.$Name.yml", "docker-compose.$Name.yaml", "examples/$Name/docker-compose.yml", "examples/$Name/docker-compose.yaml", "examples/$Name/compose.yml", "examples/$Name/compose.yaml")
    foreach ($candidate in $candidates) { if (Test-Path $candidate) { return $candidate } }
    return $null
}
function Prompt-OverlayDefaults([string]$File) {
    $text = [IO.File]::ReadAllText((Resolve-Path $File))
    $seen = @{}
    foreach ($match in [regex]::Matches($text, '\$\{([A-Z][A-Z0-9_]*):-([^}]+)\}')) {
        $key = $match.Groups[1].Value; $default = $match.Groups[2].Value
        if ($seen[$key]) { continue }; $seen[$key] = $true
        if ([string]::IsNullOrWhiteSpace((Get-EnvValue $key))) { Set-EnvValue $key (Ask-Value $key $default) }
    }
}
function Choose-Example {
    $script:ExampleName = ''; $script:ExampleCompose = ''; $script:ExampleProfile = ''
    Set-EnvValue OPENNVR_EXAMPLE ''; Set-EnvValue OPENNVR_EXAMPLE_COMPOSE ''; Set-EnvValue OPENNVR_EXAMPLE_PROFILE ''
    Write-Host ''
    Write-Host '  -- Example app ----------------------------------------'
    Info 'Examples add an AI app on top of the core NVR. The Camera Agent lets you'
    Info 'ask your cameras questions out loud or by chat. Everything runs locally.'
    if (-not (Ask-YesNo 'Set up an example app now?' $false)) { return }
    $examples = @(Get-ChildItem 'examples' -Directory | Sort-Object Name)
    if ($examples.Count -eq 0) { Warn 'No examples were found'; return }
    Write-Host ''; Info 'Available examples:'
    for ($i=0; $i -lt $examples.Count; $i++) {
        $manifest = Find-ExampleCompose $examples[$i].Name
        $status = if ($manifest) { "installable: $manifest" } else { 'no Compose manifest' }
        Write-Host ('  {0,2}. {1,-30} [{2}]' -f ($i+1), $examples[$i].Name, $status)
    }
    Write-Host '   0. Core stack only'; Write-Host ''
    $choiceRaw = Read-Host '  Select an example [0]'; if ([string]::IsNullOrWhiteSpace($choiceRaw)) { $choiceRaw = '0' }
    $choice = 0; if (-not [int]::TryParse($choiceRaw, [ref]$choice)) { Fail 'Invalid selection' }
    if ($choice -eq 0) { return }
    if ($choice -lt 1 -or $choice -gt $examples.Count) { Fail 'Selection out of range' }
    $name = $examples[$choice-1].Name; $manifest = Find-ExampleCompose $name
    if (-not $manifest) { Fail "The '$name' example has no Docker Compose manifest" }
    # $prof, not $profile — $PROFILE is an automatic PowerShell variable.
    $prof = $name
    if ($name -eq 'camera-agent') {
        Write-Host ''
        Explain 'Camera Agent runs in VOICE mode (speak, hear spoken answers) or CHAT mode (type, read answers). Voice adds Whisper speech-to-text and Piper text-to-speech; chat is lighter.' 'pick one' '1 (voice)'
        $mode = Ask-Value 'Camera Agent mode: 1=voice, 2=chat' '1'
        $prof = if ($mode -eq '2') { 'camera-agent-chat' } else { 'camera-agent' }

        Write-Host ''
        Write-Host '  -- Camera Agent models (all local, no API keys) -------'
        Configure-Value OLLAMA_MODEL 'Local LLM model (Ollama)' 'qwen2.5:1.5b' `
            'The local chat model that answers your questions; must support tool calling.' 'yes' `
            'Pulled automatically. qwen2.5:0.5b (low RAM) | 1.5b (default) | 3b (better, slower).'
        if ($prof -eq 'camera-agent') {
            Configure-Value WHISPER_MODEL_SIZE 'Whisper speech-to-text model' 'base.en' `
                'Transcribes your spoken questions (voice mode only).' 'yes' `
                'tiny.en (fastest) | base.en (default) | small.en (most accurate).'
        }
        Configure-Value CAPTION_ADAPTER 'Scene-description model' 'moondream' `
            'Describes what a camera sees. moondream answers questions (VQA); blip writes plain captions.' 'yes' `
            'moondream | blip - both run locally.'
    } else {
        Prompt-OverlayDefaults $manifest
    }
    $script:ExampleName=$name; $script:ExampleCompose=$manifest; $script:ExampleProfile=$prof
    Set-EnvValue OPENNVR_EXAMPLE $name; Set-EnvValue OPENNVR_EXAMPLE_COMPOSE $manifest; Set-EnvValue OPENNVR_EXAMPLE_PROFILE $prof
    Ok "Selected $name ($prof)"
    if ($name -eq 'camera-agent') {
        Info 'The local LLM model downloads on first start - usually the slowest step.'
    }
}
function Pull-AndBuild {
    Write-Host ''
    Info 'First-time setup downloads several container images (and, for the'
    Info 'Camera Agent, a local LLM model of ~1 GB). Depending on your network'
    Info 'this can take 8-15 minutes. Later starts are much faster - everything'
    Info 'is cached, so you only pay this cost once.'
    Write-Host ''
    Info 'Pulling the OpenNVR core stack...'
    docker compose -f $BaseCompose pull --ignore-buildable
    if ($LASTEXITCODE -ne 0) { Fail 'Failed to pull the core stack' }
    Choose-Example
    $script:ComposeArgs = @('-f', $BaseCompose)
    if ($script:ExampleCompose) {
        $script:ComposeArgs += @('-f', $script:ExampleCompose, '--profile', $script:ExampleProfile)
        Info "Pulling images for $script:ExampleName..."
        docker compose @script:ComposeArgs pull --ignore-buildable
        if ($LASTEXITCODE -ne 0) { Fail "Failed to pull $script:ExampleName" }
    }
    Info 'Building services that do not publish a pre-built image...'
    docker compose @script:ComposeArgs build
    if ($LASTEXITCODE -ne 0) { Fail 'Docker build failed' }
}

Show-Logo
Write-Host '  OpenNVR interactive installer'; Write-Host ''
Detect-Platform
Check-Prerequisites
Prepare-Environment
Pull-AndBuild
Write-Host ''; Info 'Configuration and images are ready. Starting OpenNVR...'; Write-Host ''
if ($script:Platform -eq 'Windows') { & (Join-Path $ProjectRoot 'start.ps1') up; exit $LASTEXITCODE }
& bash (Join-Path $ProjectRoot 'start.sh') up
exit $LASTEXITCODE