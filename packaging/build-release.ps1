param(
    [string]$ToolRoot='D:\programfile\RustStudioToolchain',
    [string]$Compiler='D:\programfile\ClaudeSwitchBuildTools\InnoSetup6\ISCC.exe',
    [string]$OutputDir='',
    [switch]$SkipCompile
)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
if(-not $OutputDir){$OutputDir=Join-Path $root 'dist'}
$downloads=Join-Path $PSScriptRoot 'downloads';[void][IO.Directory]::CreateDirectory($downloads)
$runtimeZip=Join-Path $downloads 'python-3.14.10-embed-amd64.zip'
if(-not(Test-Path -LiteralPath $runtimeZip)){Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.14.10/python-3.14.10-embed-amd64.zip' -OutFile $runtimeZip}
if((Get-FileHash -LiteralPath $runtimeZip -Algorithm SHA256).Hash -ne '90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159'){throw 'Embedded Python does not match its upstream SHA-256.'}
$runtime=Join-Path $PSScriptRoot 'runtime-python'
if(-not(Test-Path -LiteralPath $runtime)){Expand-Archive -LiteralPath $runtimeZip -DestinationPath $runtime}
if(-not $SkipCompile){
    & (Join-Path $root 'build.ps1') -ToolRoot $ToolRoot
    if($LASTEXITCODE){throw 'Rust compilation failed.'}
}
$launcherBin=Join-Path $root 'launcher\bin';[void][IO.Directory]::CreateDirectory($launcherBin)
$csc=Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $csc /nologo /target:winexe /platform:anycpu /optimize+ ('/win32icon:'+ (Join-Path $root 'assets\app.ico')) ('/win32manifest:'+(Join-Path $root 'launcher\app.manifest')) /reference:System.Windows.Forms.dll ('/out:'+(Join-Path $launcherBin 'ClaudeSwitchStudio.exe')) (Join-Path $root 'launcher\Program.cs')
if($LASTEXITCODE){throw 'Windows launcher compilation failed.'}
& (Join-Path $PSScriptRoot 'collect-licenses.ps1') -ToolRoot $ToolRoot -CompilerRoot (Split-Path $Compiler -Parent)
& (Join-Path $runtime 'python.exe') (Join-Path $PSScriptRoot 'render-manual.py')
if($LASTEXITCODE){throw 'Offline manual rendering failed.'}
$payload=Join-Path $PSScriptRoot 'payload'
if(Test-Path -LiteralPath $payload){throw 'Payload already exists. Preserve it and choose a fresh build checkout rather than recursively deleting it.'}
[void][IO.Directory]::CreateDirectory($payload)
foreach($name in @('Studio.ps1','Studio.xaml','studio-core.ps1','studio-worker.ps1','local_adapter.py','claude-switch-core.exe','README.md','README.zh-CN.md','CONTRIBUTORS.md','AGENTS.md','LICENSE','UPSTREAM-LICENSE','THIRD_PARTY_NOTICES.md')){Copy-Item -LiteralPath (Join-Path $root $name) -Destination $payload}
foreach($directory in @('assets','docs','third-party-licenses')){Copy-Item -LiteralPath (Join-Path $root $directory) -Destination $payload -Recurse}
Copy-Item -LiteralPath (Join-Path $launcherBin 'ClaudeSwitchStudio.exe') -Destination $payload
[void][IO.Directory]::CreateDirectory((Join-Path $payload 'runtime'))
Copy-Item -LiteralPath $runtime -Destination (Join-Path $payload 'runtime\python') -Recurse
$launcherCheck=Start-Process -FilePath (Join-Path $payload 'ClaudeSwitchStudio.exe') -ArgumentList '--self-check' -Wait -PassThru -WindowStyle Hidden
if($launcherCheck.ExitCode){throw 'Launcher dependency check failed.'}
& (Join-Path $payload 'runtime\python\python.exe') -c 'import sqlite3,ssl,hashlib,uuid,msvcrt; assert sqlite3.sqlite_version; print(ssl.OPENSSL_VERSION)'
if($LASTEXITCODE){throw 'Embedded runtime verification failed.'}
& (Join-Path $root 'installer\build-installer.ps1') -Compiler $Compiler -PayloadDir $payload -OutputDir $OutputDir -ChineseLanguageFile (Join-Path $root 'installer\ChineseSimplified.isl') -Version '0.3.1'
