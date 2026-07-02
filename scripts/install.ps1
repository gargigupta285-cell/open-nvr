# OpenNVR interactive installer for Windows (also detects PowerShell on Linux/macOS).
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BaseCompose = 'docker-compose.yml'
Set-Location $ProjectRoot

function Info([string]$Message) { Write-Host "  $Message" }
function Ok([string]$Message) { Write-Host "  ✓ $Message" -ForegroundColor Green }
function Warn([string]$Message) { Write-Host "  ⚠ $Message" -ForegroundColor Yellow }
function Fail([string]$Message) { throw $Message }
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

function Detect-Platform {
    if ($IsLinux) { $script:Platform = 'Linux'; $script:DefaultRecordings = '/var/lib/opennvr/recordings' }
    elseif ($IsMacOS) { $script:Platform = 'macOS'; $script:DefaultRecordings = '/Users/Shared/opennvr-recordings' }
    else { $script:Platform = 'Windows'; $script:DefaultRecordings = 'C:/opennvr/recordings' }
    Ok "Detected $script:Platform (Docker bridge mode)"
}
function Check-Prerequisites {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail 'Docker is not installed' }
    docker compose version *> $null
    if ($LASTEXITCODE -ne 0) { Fail 'Docker Compose v2 is required' }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { Fail 'Docker is not running' }
    if (-not (Test-Path $BaseCompose)) { Fail "$BaseCompose was not found in $ProjectRoot" }
}

function Get-EnvValue([string]$Key) {
    if (-not (Test-Path '.env')) { return '' }
    $line = Get-Content '.env' | Where-Object { $_ -match ('^' + [regex]::Escape($Key) + '=') } | Select-Object -Last 1
    if (-not $line) { return '' }
    $value = ($line -split '=', 2)[1] -replace '\s+#.*$', ''
    return $value.Trim().Trim('"').Trim("'")
}
function Set-EnvValue([string]$Key, [string]$Value) {
    $lines = if (Test-Path '.env') { [Collections.Generic.List[string]](Get-Content '.env') } else { [Collections.Generic.List[string]]::new() }
    $pattern = '^' + [regex]::Escape($Key) + '='
    $output = [Collections.Generic.List[string]]::new(); $written = $false
    foreach ($line in $lines) {
        if ($line -match $pattern) {
            if (-not $written) { $output.Add("$Key=$Value"); $written = $true }
        } else { $output.Add($line) }
    }
    if (-not $written) { $output.Add(''); $output.Add("$Key=$Value") }
    [IO.File]::WriteAllLines((Join-Path $ProjectRoot '.env'), $output, [Text.UTF8Encoding]::new($false))
}
function Test-MissingOrPlaceholder([string]$Value) {
    return [string]::IsNullOrWhiteSpace($Value) -or $Value -match '^(dev_|insecure_|change_me|your_|changeme|placeholder|dummy|CKLghtP4rWz8J9vN2xQ5mT7yU8kF6bD3eH1aG4cS0wE=)'
}
function New-RandomBytes([int]$Count) { $b = New-Object byte[] $Count; $rng = [Security.Cryptography.RandomNumberGenerator]::Create(); try { $rng.GetBytes($b) } finally { $rng.Dispose() }; return $b }
function New-Hex([int]$Bytes) { return ((New-RandomBytes $Bytes) | ForEach-Object { $_.ToString('x2') }) -join '' }
function New-Password { return [Convert]::ToBase64String((New-RandomBytes 36)).Replace('+','').Replace('/','').Replace('=','').Substring(0,32) }
function New-FernetKey { return [Convert]::ToBase64String((New-RandomBytes 32)).Replace('+','-').Replace('/','_') }
function Ensure-PlainValue([string]$Key, [string]$Label, [string]$Default) {
    if (-not [string]::IsNullOrWhiteSpace((Get-EnvValue $Key))) { return }
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
    } else { Ok 'Using existing .env; existing values will be preserved' }
    Ensure-PlainValue POSTGRES_USER 'PostgreSQL user' 'opennvr_user'
    Ensure-PlainValue POSTGRES_DB 'PostgreSQL database' 'opennvr_db'
    Ensure-PlainValue RECORDINGS_PATH 'Recordings path' $script:DefaultRecordings
    Ensure-PlainValue DEFAULT_ADMIN_USERNAME 'Administrator username' 'admin'
    Ensure-PlainValue DEFAULT_ADMIN_EMAIL 'Administrator email' 'admin@opennvr.local'
    Ensure-SecretValue POSTGRES_PASSWORD 'PostgreSQL password' (New-Password)
    Ensure-SecretValue SECRET_KEY 'JWT signing key' (New-Hex 32)
    Ensure-SecretValue CREDENTIAL_ENCRYPTION_KEY 'credential encryption key' (New-FernetKey)
    Ensure-SecretValue INTERNAL_API_KEY 'internal API key' (New-Password)
    Ensure-SecretValue MEDIAMTX_SECRET 'MediaMTX webhook secret' (New-Hex 32)
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
    if (-not (Ask-YesNo 'Install an example AI stack?' $false)) { return }
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
    $profile = $name
    if ($name -eq 'camera-agent') {
        $mode = Ask-Value 'Camera agent mode: 1=voice, 2=chat' '1'
        $profile = if ($mode -eq '2') { 'camera-agent-chat' } else { 'camera-agent' }
    }
    Prompt-OverlayDefaults $manifest
    $script:ExampleName=$name; $script:ExampleCompose=$manifest; $script:ExampleProfile=$profile
    Set-EnvValue OPENNVR_EXAMPLE $name; Set-EnvValue OPENNVR_EXAMPLE_COMPOSE $manifest; Set-EnvValue OPENNVR_EXAMPLE_PROFILE $profile
    Ok "Selected $name ($profile)"
}
function Pull-AndBuild {
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

Write-Host ''; Write-Host 'OpenNVR interactive installer'; Write-Host ''
Detect-Platform
Check-Prerequisites
Prepare-Environment
Pull-AndBuild
Write-Host ''; Info 'Configuration and images are ready. Starting OpenNVR...'; Write-Host ''
if ($script:Platform -eq 'Windows') { & (Join-Path $ProjectRoot 'start.ps1') up; exit $LASTEXITCODE }
& bash (Join-Path $ProjectRoot 'start.sh') up
exit $LASTEXITCODE