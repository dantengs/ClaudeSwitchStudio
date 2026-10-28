param([Parameter(Mandatory=$true)][string]$RequestPath,[Parameter(Mandatory=$true)][string]$ResponsePath)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'studio-core.ps1')
$native=Join-Path $PSScriptRoot 'claude-switch-core.exe'
if(-not(Test-Path -LiteralPath $native)){
    Write-Json $ResponsePath @{Ok=$false;Error='缺少Rust核心，已停止。不会运行旧版账号操作。'}
    exit 1
}
& $native --request $RequestPath --response $ResponsePath --base $PSScriptRoot
exit $LASTEXITCODE