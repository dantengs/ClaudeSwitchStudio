param(
    [string]$Compiler='D:\programfile\ClaudeSwitchBuildTools\InnoSetup6\ISCC.exe',
    [string]$PayloadDir=(Join-Path (Split-Path $PSScriptRoot -Parent) 'packaging\payload'),
    [string]$OutputDir=(Join-Path (Split-Path $PSScriptRoot -Parent) 'dist'),
    [string]$ChineseLanguageFile='',
    [string]$Version='0.4.3-preview'
)
$ErrorActionPreference='Stop'
# An optional lowercase suffix marks local preview builds; Windows file versions stay numeric.
if($Version -notmatch '^(\d+\.\d+\.\d+)(-[a-z0-9]+)?$'){throw 'Invalid release version.'}
$numericVersion=$Matches[1]
if(-not(Test-Path -LiteralPath $Compiler -PathType Leaf)){throw 'Inno Setup compiler not found. Pass -Compiler with the verified ISCC.exe path.'}
$compilerPath=(Resolve-Path -LiteralPath $Compiler).Path
$payloadRoot=(Resolve-Path -LiteralPath $PayloadDir).Path.TrimEnd('\')
if(((Get-Item -LiteralPath $payloadRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Payload root must not be a directory link.'}
$outputRoot=[IO.Path]::GetFullPath($OutputDir)
if($outputRoot.StartsWith($payloadRoot+'\',[StringComparison]::OrdinalIgnoreCase) -or $outputRoot -eq $payloadRoot){throw 'Output must be outside the payload.'}
if(-not $ChineseLanguageFile){$ChineseLanguageFile=Join-Path (Split-Path $compilerPath -Parent) 'Languages\ChineseSimplified.isl'}
if(-not(Test-Path -LiteralPath $ChineseLanguageFile -PathType Leaf)){throw 'Official ChineseSimplified.isl missing. Pass -ChineseLanguageFile explicitly.'}
$ChineseLanguageFile=(Resolve-Path -LiteralPath $ChineseLanguageFile).Path

$required=@('ClaudeSwitchStudio.exe','claude-switch-core.exe','Studio.ps1','Studio.xaml','studio-core.ps1','local_adapter.py','assets\app.ico','runtime\python\python.exe')
foreach($relative in $required){if(-not(Test-Path -LiteralPath (Join-Path $payloadRoot $relative) -PathType Leaf)){throw ('Required release file missing: '+$relative)}}

# Enumerate manually and reject links before descending. Get-ChildItem -Recurse could
# follow a junction into a user's account data before it gets a chance to be rejected.
$directories=New-Object 'Collections.Generic.Stack[string]'
$directories.Push($payloadRoot)
$files=New-Object 'Collections.Generic.List[object]'
while($directories.Count){
    $folder=$directories.Pop()
    foreach($item in Get-ChildItem -LiteralPath $folder -Force){
        if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw ('Payload contains a link: '+$item.Name)}
        $relative=$item.FullName.Substring($payloadRoot.Length+1)
        $parts=$relative -split '[\\/]'
        foreach($part in $parts){
            if($part -in @('data','private-auth-backups','.claude','.claude-profiles','projects','claude-code-sessions','Local Storage','Session Storage','Network','source-backups','acceptance','requests')){throw ('Runtime or private data in payload: '+$relative)}
        }
        if($item.PSIsContainer){$directories.Push($item.FullName);continue}
        if($item.Extension -eq '.jsonl' -or $item.Name -in @('.credentials.json','claude.json','.claude.json','config.json','claude_desktop_config.json','relay.json','identities.json','labels.json','selected-provider.json','studio-profile.json','Local State') -or $item.Name -match '\.(request|response)\.json$'){throw ('Sensitive or runtime file in payload: '+$relative)}
        $files.Add([ordered]@{Path=$relative;Bytes=$item.Length;Sha256=(Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash})
    }
}
[void][IO.Directory]::CreateDirectory($outputRoot)
$expectedInstaller=Join-Path $outputRoot ('ClaudeSwitchStudio-'+$Version+'-Setup.exe')
if(Test-Path -LiteralPath $expectedInstaller){throw 'Installer output already exists; use a fresh output folder rather than overwriting a released artifact.'}
$manifest=[ordered]@{Version=$Version;CreatedAt=[DateTimeOffset]::Now.ToString('o');PrivateRuntimeIncluded=$false;Files=@($files.ToArray()|Sort-Object Path)}
$manifestPath=Join-Path $outputRoot ('payload-'+$Version+'-manifest.json')
[IO.File]::WriteAllText($manifestPath,($manifest|ConvertTo-Json -Depth 8),(New-Object Text.UTF8Encoding($false)))
& $compilerPath ('/DAppVersion='+$Version) ('/DNumericVersion='+$numericVersion) ('/DPayloadDir='+$payloadRoot) ('/DBuildOutputDir='+$outputRoot) ('/DChineseLanguageFile='+$ChineseLanguageFile) (Join-Path $PSScriptRoot 'Studio.iss')
if($LASTEXITCODE -ne 0 -or -not(Test-Path -LiteralPath $expectedInstaller)){throw 'Installer compilation failed.'}
foreach($file in $files){if((Get-FileHash -LiteralPath (Join-Path $payloadRoot $file.Path) -Algorithm SHA256).Hash -ne $file.Sha256){throw ('Payload changed during compilation: '+$file.Path)}}
$hash=(Get-FileHash -LiteralPath $expectedInstaller -Algorithm SHA256).Hash
$hash+'  '+(Split-Path $expectedInstaller -Leaf)|Set-Content -LiteralPath ($expectedInstaller+'.sha256') -Encoding ASCII
[pscustomobject]@{Installer=$expectedInstaller;Sha256=$hash;PayloadFiles=$files.Count;Manifest=$manifestPath;CodeSigned=$false}|ConvertTo-Json
