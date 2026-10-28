param([string]$ToolRoot='D:\programfile\RustStudioToolchain',[string]$CompilerRoot='D:\programfile\ClaudeSwitchBuildTools\InnoSetup6')
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$env:CARGO_HOME=Join-Path $ToolRoot 'cargo';$env:RUSTUP_HOME=Join-Path $ToolRoot 'rustup'
$cargo=Join-Path $env:CARGO_HOME 'bin\cargo.exe'
$raw=& $cargo metadata --offline --locked --format-version 1 --filter-platform x86_64-pc-windows-gnu --manifest-path (Join-Path $root 'rust-core\Cargo.toml')
if($LASTEXITCODE){throw 'Cannot resolve the exact Windows dependency licenses.'}
$metadata=($raw -join "`n")|ConvertFrom-Json
$licenses=Join-Path $root 'third-party-licenses'
[void][IO.Directory]::CreateDirectory($licenses)
$lines=New-Object 'Collections.Generic.List[string]'
$lines.Add('# Third-party notices')
$lines.Add('')
$lines.Add('Original project code: MIT; third-party material keeps its own license. See LICENSE and docs/OPEN_SOURCE_AUDIT.md. The historical Sevin47 prototype notice is retained in UPSTREAM-LICENSE. CC Switch and the other researched applications are not bundled.')
$lines.Add('')
$lines.Add('## Rust application dependencies')
$lines.Add('')
$lines.Add('For each package below the project elects the MIT option where the upstream offers alternatives. Original license files (including alternative texts and notices where present) are preserved, unmodified, in third-party-licenses. Build dependencies are included for completeness.')
$lines.Add('')
$lines.Add('| Package | Version | Upstream license expression | Notices directory |')
$lines.Add('| --- | --- | --- | --- |')
foreach($package in @($metadata.packages | Where-Object {$_.name -ne 'claude-switch-core'} | Sort-Object name)){
    if($package.license -notmatch 'MIT'){throw ('Review the new license before release: '+$package.name)}
    $directory=Split-Path $package.manifest_path -Parent
    $noticeFiles=@(Get-ChildItem -LiteralPath $directory -File | Where-Object {$_.Name -match '^(LICEN[CS]E|COPYING|NOTICE|AUTHORS)([._-]|$)'})
    $target=Join-Path $licenses ('rust-crates\'+$package.name+'-'+$package.version)
    [void][IO.Directory]::CreateDirectory($target)
    if($noticeFiles.Count){foreach($file in $noticeFiles){Copy-Item -LiteralPath $file.FullName -Destination $target -Force}}
    elseif($package.name -eq 'winapi-x86_64-pc-windows-gnu'){
        $winapi=$metadata.packages|Where-Object {$_.name -eq 'winapi'}|Select-Object -First 1
        foreach($name in @('LICENSE-MIT','LICENSE-APACHE')){Copy-Item -LiteralPath (Join-Path (Split-Path $winapi.manifest_path -Parent) $name) -Destination $target -Force}
        [IO.File]::WriteAllText((Join-Path $target 'SOURCE.txt'),'The import-library crate is from retep998/winapi-rs and declares MIT/Apache-2.0. Its package omits the license files; these unmodified files are from winapi 0.3.9 in the same upstream repository.',(New-Object Text.UTF8Encoding($false)))
    }else{throw ('Missing upstream license text: '+$package.name)}
    $lines.Add('| '+$package.name+' | '+$package.version+' | '+$package.license+' | `rust-crates/'+$package.name+'-'+$package.version+'` |')
}
$rustDocs=Join-Path $env:RUSTUP_HOME 'toolchains\stable-x86_64-pc-windows-gnu\share\doc\rust'
$rustTarget=Join-Path $licenses 'rust-toolchain';[void][IO.Directory]::CreateDirectory($rustTarget)
foreach($name in @('COPYRIGHT-library.html','COPYRIGHT.html')){Copy-Item -LiteralPath (Join-Path $rustDocs $name) -Destination $rustTarget -Force}
Copy-Item -LiteralPath (Join-Path $rustDocs 'licenses') -Destination $rustTarget -Recurse -Force
$pythonTarget=Join-Path $licenses 'python';[void][IO.Directory]::CreateDirectory($pythonTarget)
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'runtime-python\LICENSE.txt') -Destination $pythonTarget -Force
$downloads=@(
    @{Url='https://docs.python.org/3.14/license.html';Path=(Join-Path $pythonTarget 'Licenses-and-Acknowledgements.html')},
    @{Url='https://raw.githubusercontent.com/openssl/openssl/openssl-3.5.9/LICENSE.txt';Path=(Join-Path $pythonTarget 'OpenSSL-3.5.9-LICENSE.txt')},
    @{Url='https://raw.githubusercontent.com/openssl/openssl/openssl-3.5.9/ACKNOWLEDGEMENTS.md';Path=(Join-Path $pythonTarget 'OpenSSL-3.5.9-ACKNOWLEDGEMENTS.md')},
    @{Url='https://raw.githubusercontent.com/openssl/openssl/openssl-3.5.9/AUTHORS.md';Path=(Join-Path $pythonTarget 'OpenSSL-3.5.9-AUTHORS.md')}
)
foreach($download in $downloads){if(-not(Test-Path -LiteralPath $download.Path)){Invoke-WebRequest -Uri $download.Url -OutFile $download.Path}}
$innoTarget=Join-Path $licenses 'inno-setup';[void][IO.Directory]::CreateDirectory($innoTarget)
Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/jrsoftware/issrc/is-6_7_3/license.txt' -OutFile (Join-Path $innoTarget 'LICENSE.txt')
$lines.Add('')
$lines.Add('## Runtime and packaging')
$lines.Add('')
$lines.Add('- Rust standard library and toolchain runtime notices: `third-party-licenses/rust-toolchain/`, with the upstream COPYRIGHT pages and their referenced license texts, including the GCC Runtime Library Exception where applicable. These notices do not relicense this application as GPL.')
$lines.Add('- Python 3.14.10 Windows x64 embedded distribution: `runtime/python/LICENSE.txt`, `third-party-licenses/python/LICENSE.txt`, and the upstream Licenses and Acknowledgements document. Python and its Windows distributable code retain their original conditions; do not remove notices or redistribute the Windows runtime on unsupported platforms.')
$lines.Add('- The Python distribution includes OpenSSL 3.5.9. Its unmodified Apache-2.0 license, AUTHORS and ACKNOWLEDGEMENTS are separately retained under `third-party-licenses/python/`; that upstream tag has no NOTICE file. Other Python component notices are retained in the official distribution license and acknowledgements document.')
$lines.Add('- Inno Setup 6.7.3: installer built with the original compiler; no compiler is bundled. Its license is retained under `third-party-licenses/inno-setup/`. Original installer copyright and website identification are not removed.')
$lines.Add('- Windows PowerShell, WPF and .NET Framework are used from Windows and are not copied into the payload. The original SVG/PNG/ICO application artwork is part of this project under its MIT license.')
$lines.Add('')
$lines.Add('Sources: https://www.python.org/downloads/release/python-31410/ ; https://docs.python.org/3.14/license.html ; https://github.com/openssl/openssl/tree/openssl-3.5.9 ; https://github.com/rust-lang/rust ; https://github.com/jrsoftware/issrc/tree/is-6_7_3')
[IO.File]::WriteAllText((Join-Path $root 'THIRD_PARTY_NOTICES.md'),($lines -join "`n")+"`n",(New-Object Text.UTF8Encoding($false)))
Write-Output ('Third-party notices collected: '+@($metadata.packages).Count+' packages including the project')
