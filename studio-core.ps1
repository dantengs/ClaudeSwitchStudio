# Minimal WPF helpers. Account/data operations are exclusively dispatched to the Rust core.
$ErrorActionPreference='Stop'
function Initialize-Studio([string]$KernelRoot,[string]$FixtureRoot){
    $script:Kernel=$KernelRoot
    $script:Data=Join-Path $PSScriptRoot 'data'
}
function Read-Json([string]$Path){
    if(-not(Test-Path -LiteralPath $Path)){return $null}
    return [IO.File]::ReadAllText($Path,[Text.Encoding]::UTF8)|ConvertFrom-Json
}
function Write-Json([string]$Path,$Value){
    $parent=Split-Path $Path -Parent
    if(-not(Test-Path -LiteralPath $parent)){[void][IO.Directory]::CreateDirectory($parent)}
    $temporary=$Path+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
    [IO.File]::WriteAllText($temporary,($Value|ConvertTo-Json -Depth 35),(New-Object Text.UTF8Encoding($false)))
    if(Test-Path -LiteralPath $Path){[IO.File]::Replace($temporary,$Path,[NullString]::Value)}else{[IO.File]::Move($temporary,$Path)}
}
function Prop($Object,[string]$Name,$Default=$null){
    if($null -ne $Object -and $null -ne $Object.PSObject.Properties[$Name]){return $Object.$Name}
    return $Default
}
function Check-Profile([string]$Name){
    if($Name -notmatch '^[\p{L}\p{N}][\p{L}\p{N}_ -]{0,39}$' -or $Name.EndsWith(' ') -or $Name -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$'){
        throw '名称请使用1–40个中英文、数字、空格、短横线或下划线。'
    }
}