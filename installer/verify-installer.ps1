param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$PayloadManifest,
    [string]$RunRoot=(Join-Path ([IO.Path]::GetTempPath()) ('ClaudeSwitchSetupAcceptance-'+[guid]::NewGuid().ToString('N')))
)
$ErrorActionPreference='Stop'
$installerPath=(Resolve-Path -LiteralPath $Installer).Path
$manifest=Get-Content -LiteralPath $PayloadManifest -Raw -Encoding UTF8 | ConvertFrom-Json
if(-not $manifest.Version -or -not @($manifest.Files).Count -or $manifest.PrivateRuntimeIncluded -ne $false){throw 'A validated build payload manifest is required.'}
$tempRoot=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$runPath=[IO.Path]::GetFullPath($RunRoot).TrimEnd('\')
if(-not $runPath.StartsWith($tempRoot+'\',[StringComparison]::OrdinalIgnoreCase) -or (Split-Path $runPath -Leaf) -notmatch '^ClaudeSwitchSetupAcceptance-[a-f0-9]{32}$'){throw 'Acceptance must use a new, explicitly named directory inside the Windows temporary directory.'}
if(Test-Path -LiteralPath $runPath){throw 'Acceptance directory already exists; a fresh directory is required.'}
$registryPath='HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6115D5A1-F07B-49F4-B41D-FCB71896493A}_is1'
if(Test-Path -LiteralPath $registryPath){throw 'A registered installation already exists. /DIR alone does not isolate AppId: do not run an acceptance uninstall against this user profile.'}
[void][IO.Directory]::CreateDirectory($runPath)
$installPath=Join-Path $runPath 'app'

function Assert-OwnedRegistration {
    if(-not(Test-Path -LiteralPath $registryPath)){throw 'Sample uninstall registration was not created.'}
    $location=(Get-ItemProperty -LiteralPath $registryPath).InstallLocation
    if(-not $location -or [IO.Path]::GetFullPath($location).TrimEnd('\') -ne $installPath){throw 'Uninstall registration does not point to this sample; refusing to continue.'}
}
function Assert-InstalledFiles {
    foreach($file in $manifest.Files){
        $target=[IO.Path]::GetFullPath((Join-Path $installPath $file.Path))
        if(-not $target.StartsWith($installPath+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Manifest target escapes the sample directory.'}
        if(-not(Test-Path -LiteralPath $target -PathType Leaf) -or (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $file.Sha256){throw ('Installed hash mismatch: '+$file.Path)}
    }
}
function Install-Sample([string]$Stage) {
    $log=Join-Path $runPath ($Stage+'.log')
    # /NOICONS and the empty task list prevent test shortcuts from taking over a
    # real shortcut. This still uses the actual stable AppId, guarded above.
    $arguments='/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOICONS /TASKS="" /DIR="'+$installPath+'" /LOG="'+$log+'"'
    $process=Start-Process -FilePath $installerPath -ArgumentList $arguments -PassThru -Wait -WindowStyle Hidden
    if($process.ExitCode -notin @(0,3010)){throw ('Sample setup failed: '+$process.ExitCode+'; see '+$log)}
    Assert-OwnedRegistration
    Assert-InstalledFiles
}

Install-Sample 'first-install'
$markerDirectory=Join-Path $installPath 'data\backups\acceptance-owned-sample'
[void][IO.Directory]::CreateDirectory($markerDirectory)
$marker=Join-Path $markerDirectory 'user-data-marker.txt'
$contents='This is owned synthetic data. No account credentials or conversations are used. '+[guid]::NewGuid().ToString('N')
[IO.File]::WriteAllText($marker,$contents,(New-Object Text.UTF8Encoding($false)))
$markerHash=(Get-FileHash -LiteralPath $marker -Algorithm SHA256).Hash
Install-Sample 'reinstall-data-retention'
if((Get-FileHash -LiteralPath $marker -Algorithm SHA256).Hash -ne $markerHash){throw 'Reinstallation changed user-created data.'}
Assert-OwnedRegistration
$uninstallers=@(Get-ChildItem -LiteralPath $installPath -File -Filter 'unins*.exe' | Where-Object {$_.Name -match '^unins\d{3}\.exe$'})
if($uninstallers.Count -ne 1){throw 'Expected exactly one sample-owned uninstaller.'}
$uninstaller=$uninstallers[0].FullName
if(-not $uninstaller.StartsWith($installPath+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Uninstaller escapes the verified sample path.'}
$uninstallLog=Join-Path $runPath 'uninstall.log'
$uninstallArguments='/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="'+$uninstallLog+'"'
$process=Start-Process -FilePath $uninstaller -ArgumentList $uninstallArguments -PassThru -Wait -WindowStyle Hidden
if($process.ExitCode -notin @(0,3010)){throw ('Sample uninstall failed: '+$process.ExitCode)}
if(Test-Path -LiteralPath (Join-Path $installPath 'ClaudeSwitchStudio.exe')){throw 'Uninstall retained the installed launcher.'}
if(Test-Path -LiteralPath (Join-Path $installPath 'Studio.ps1')){throw 'Uninstall retained the installed application script.'}
if(-not(Test-Path -LiteralPath $marker) -or (Get-FileHash -LiteralPath $marker -Algorithm SHA256).Hash -ne $markerHash){throw 'Uninstall changed user-created data.'}
if(Test-Path -LiteralPath $registryPath){throw 'Sample uninstall registration was not removed.'}
$result=[ordered]@{Version=$manifest.Version;Time=[DateTimeOffset]::Now.ToString('o');Run=$runPath;PayloadFilesVerified=@($manifest.Files).Count;ReinstallPreservedSyntheticData=$true;UninstallPreservedSyntheticData=$true;SampleRegistrationRemoved=$true;ApplicationLaunched=$false;ClaudeLaunched=$false;AccountDataAccessed=$false;RealInstallationRequiredAbsent=$true;RecursiveCleanupPerformed=$false}
[IO.File]::WriteAllText((Join-Path $runPath 'acceptance.json'),($result|ConvertTo-Json -Depth 6),(New-Object Text.UTF8Encoding($false)))
$result|ConvertTo-Json
