param([string]$ToolRoot='D:\programfile\RustStudioToolchain',[switch]$SkipTests)
$ErrorActionPreference='Stop'
$env:CARGO_HOME=Join-Path $ToolRoot 'cargo'
$env:RUSTUP_HOME=Join-Path $ToolRoot 'rustup'
$cargo=Join-Path $env:CARGO_HOME 'bin\cargo.exe'
$toolchain=Join-Path $env:RUSTUP_HOME 'toolchains\stable-x86_64-pc-windows-gnu'
$native=Join-Path $toolchain 'lib\rustlib\x86_64-pc-windows-gnu\bin\self-contained'
$env:PATH=$native+';'+$env:PATH
$env:CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER=Join-Path $native 'x86_64-w64-mingw32-gcc.exe'
# The bundled GNU linker cannot handle Chinese build output paths reliably.
$env:CARGO_TARGET_DIR=Join-Path $ToolRoot 'build\claude-switch-0.2'
$manifest=Join-Path $PSScriptRoot 'rust-core\Cargo.toml'
if(-not $SkipTests){& $cargo test --locked --manifest-path $manifest;if($LASTEXITCODE){throw 'Rust tests failed'}}
& $cargo build --release --locked --manifest-path $manifest
if($LASTEXITCODE){throw 'Rust build failed'}
Copy-Item -LiteralPath (Join-Path $env:CARGO_TARGET_DIR 'release\claude-switch-core.exe') -Destination (Join-Path $PSScriptRoot 'claude-switch-core.exe') -Force
Write-Host 'Rust draft built successfully.'
