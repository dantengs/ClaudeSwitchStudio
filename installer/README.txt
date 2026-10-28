Building the Windows installer

1. Build the 0.3.1 application payload in ../packaging/payload. It must include:
   ClaudeSwitchStudio.exe, claude-switch-core.exe, Studio.ps1, Studio.xaml,
   studio-core.ps1, local_adapter.py, assets/app.ico, runtime/python/python.exe.
   Add only redistributable application assets, runtime libraries and license files.
   Never copy a running installation's data directory into this folder.

2. Install the official Inno Setup 6.7.3 compiler from https://jrsoftware.org/isdl.php.
   Verify the installer signature according to the publisher's instructions.
   Download ChineseSimplified.isl from the official Inno Setup translations repository
   if the compiler distribution does not provide it.

3. From Windows PowerShell:
   .\installer\build-installer.ps1 -Compiler 'path\ISCC.exe' -ChineseLanguageFile 'path\ChineseSimplified.isl'
   Optional: -PayloadDir, -OutputDir, -Version.
   The default output is ../dist/ClaudeSwitchStudio-0.3.1-Setup.exe, with a SHA-256
   sidecar and a manifest of every included file. Use a new output folder to rebuild
   an already published version. The script rejects links, login caches, conversation
   JSONL, application runtime data and private backups before compiling.

Behavior and safety boundaries

* Stable AppId: 6115D5A1-F07B-49F4-B41D-FCB71896493A.
* The installer uses the current user's privileges and registers per-user uninstall.
* Default location is D:\programfile\Claude Switch Studio. If no D drive exists,
  it falls back to the current user's LocalAppData\Programs directory.
* AppMutex is ClaudeSwitchStudio.Runtime. The native launcher must hold that mutex
  for the entire child WPF process lifetime. Setup and uninstall can then ask the user
  to close the application rather than modifying it while it is active.
* No taskkill, forced shutdown, Claude process control, migration or login action.
* CloseApplications and RestartApplications are disabled. There are no [UninstallDelete]
  rules. Inno removes only registered program files, shortcuts and its uninstall entry.
* Files generated later in {app}\data are retained after uninstall. Existing Claude
  directories, .claude-profiles and old side-by-side installations are untouched.
* The interactive Finish page has an unchecked launch option. Silent installation never
  launches the application. There are no installer or uninstaller auto-start scripts.
* No PATH, Python, file associations, scheduled tasks or system services are changed.

Isolated acceptance (do not run against a registered real installation)

Run acceptance before installing the released product on the current Windows account:
  .\installer\verify-installer.ps1 -Installer 'dist\ClaudeSwitchStudio-0.3.1-Setup.exe' -PayloadManifest 'dist\payload-0.3.1-manifest.json'

The script requires the stable AppId's current-user uninstall registration to be absent.
Passing /DIR alone is NOT isolation: an installer with the same AppId can overwrite an
existing product's uninstall registration even when its files are in a different directory.
The script refuses that situation. It installs in a new, validated Windows temporary
directory, suppresses shortcuts and automatic launch, verifies every payload hash, writes
an owned synthetic data marker, reinstalls to verify upgrade retention, and uninstalls only
after confirming the registry points to the exact sample directory. The marker must remain
and the sample uninstall registration must disappear. Logs and retained sample data are
left for review; the script performs no recursive cleanup and accesses no Claude files.

This verifies program file installation, reinstall/upgrade preservation, and uninstall data
retention. It does not validate desktop/start-menu shortcuts because the sample explicitly
suppresses them, nor does it prove account sign-in, account switching or online validity.
Test shortcuts and the utility's own read-only UI separately after installation if needed.

Open-source build portability

All payload and output defaults resolve against the repository's installer directory rather
than the shell's working directory. The default compiler location is a developer convenience;
other contributors should pass -Compiler and, if needed, -ChineseLanguageFile explicitly.
The resulting installation contains no D-drive runtime dependency: setup allows a custom
path and uses LocalAppData\Programs when the target computer has no D drive. The launcher,
icons, embedded runtime and scripts must resolve their companion files relative to the
installation directory. Runtime data is not packaged; it is generated on the user's machine.

Public release review

Publish only the generated Setup.exe and SHA-256 sidecar as binary release assets. Keep
packaging/payload, runtime downloads, local compiler paths, installation/acceptance logs,
personal identity mappings and credential backups out of Git history. The installer build
guard rejects common private filenames but is not a substitute for reviewing the exact
Git staging list and the payload manifest. Inno Setup and CPython redistribution license
notices must accompany any redistributed files; retain the application's upstream notices.

This project has no code-signing certificate. The installer and application are unsigned;
the generated SHA-256 verifies artifact integrity but does not establish publisher identity.
