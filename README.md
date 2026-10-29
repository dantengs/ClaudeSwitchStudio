<p align="center"><img src="assets/app.png" width="104" alt="Claude Switch Studio icon"></p>
<h1 align="center">Claude Switch Studio</h1>
<p align="center">A local Windows workspace for accounts, providers, conversations, and backups</p>

**[English](README.md)** | [简体中文](README.zh-CN.md)

> ❤️ If you like this project or it helps you, please give it a ⭐ [Star](https://github.com/dantengs/ClaudeSwitchStudio) — thank you!

**An independent project, not an official Anthropic product.** The current release targets Windows 10/11 x64 and combines a Rust data core, a local Python adapter, and a WPF interface.

## Download and install

Download an installer from [Releases](https://github.com/dantengs/ClaudeSwitchStudio/releases) and follow the setup wizard:

- **0.4.3-preview (pre-release)**: `ClaudeSwitchStudio-0.4.3-preview-Setup.exe`, with the unified account page, third-party accounts and access table, multiple Codex accounts, usage statistics, MCP and skill toggles, WebDAV cloud backup, and conversation titles, working folders and sidebar folders unified across accounts. It is a preview, so read the [release notes (Chinese)](RELEASE_NOTES.md) first.
- **0.3.1 (stable)**: `ClaudeSwitchStudio-0.3.1-Setup.exe`.

 The default location is `D:\programfile\Claude Switch Studio`; you can choose another directory. If drive D is unavailable, setup uses the current user's programs directory.

Open **Claude Switch Studio** from the desktop or Start menu. The installation includes an EXE launcher, original application icons, and the required Python runtime, without changing the system PATH. A three-step guide appears on first launch and can be reopened in Settings.

The installer is currently unsigned. Download it from this repository's release page and verify the accompanying `.sha256` file. Setup does not close or operate Claude. Close this utility before upgrading it.

## Features

- Discover historical Claude Desktop accounts on the local machine, with sourced email mappings and capture status.
- Keep several official Claude Desktop accounts as experimental local login snapshots, shown as account cards, and add new ones from the official sign-in page.
- One-click switching: the utility checks whether Claude Desktop is running and, after you confirm, quits it, takes a timestamped backup, switches, and reopens Claude. Independent Claude Code CLI sessions are left alone.
- Two-way conversation list sharing: while Claude is closed for a switch, each official account receives the conversations it is missing from the others, and titles, working folders and sidebar folders are unified across accounts (nothing is deleted; deletions are respected).
- Usage statistics in the style of CC Switch: last 24 hours, today, 7 or 30 days, or everything, with tokens, cache hit rate and cost by account, model and day (or hour). Claude, Codex and Antigravity usage are priced from Anthropic's, OpenAI's and Google's official pricing pages, refreshed automatically; trends are shown on a horizontal timeline.
- Codex accounts, Antigravity-style: save several ChatGPT logins for Codex, switch with the same detect-quit-reopen flow, and see each account's latest weekly/5-hour quota. Add Codex accounts through the official browser sign-in in the background, without closing Codex. Codex usage by account and model, priced from OpenAI's official page.
- One account page: official Claude accounts, official Codex accounts, and third-party accounts grouped by vendor (DeepSeek, Kimi, Zhipu GLM, Qwen, MiniMax, OpenRouter, Gemini, Antigravity Tools' local proxy, custom). An access table picks one source each for Claude Desktop, Claude Code and Codex, with a backup before every change and an exact restore when switching back. Adding a vendor account can detect the models its key can use; keys are encrypted with Windows DPAPI. Codex connects by API key, the same way CC Switch does it.
- MCP and skill toggles in the style of CC Switch: switch each MCP server and skill on or off separately for Claude Code and Codex, with select-all / clear-all, backups and restore.
- Backups go to this PC, a WebDAV server (Nutstore, Nextcloud, Synology and others), or both. Files are encrypted locally (AES-256 + HMAC-SHA256) before upload, old copies are pruned, cloud backups can be restored to this PC, and newer backups from another PC are announced at startup.
- Runs from the system tray: × hides the window, and the tray menu switches accounts, opens Claude, toggles start with Windows, or quits. Named backups that can be renamed or sent to the Recycle Bin.
- Import Claude and Codex providers read-only from an existing CC Switch installation (entries with the same key become one account). CC Switch itself is neither changed nor bundled.
- Combine local conversation indexes from official and third-party installations, deduplicate by transcript identity, and provide search and historical usage views.
- Show local project memory locations and back up transcripts, indexes, and related state.
- Light and dark themes, adjustable text size, consistent vector sidebar icons, and first-run guidance.

## Important limitations

**Login snapshots are experimental, not an official authorization interface.** This utility checks local files and caches; it cannot guarantee a valid login or freedom from account restrictions. Anthropic's rules restrict third-party applications from offering Claude.ai login, collecting or storing session tokens, and routing subscription requests on users' behalf. A local backup does not establish official permission. Read the [official credential-use guidance](https://code.claude.com/docs/en/legal-and-compliance#authentication-and-credential-use) before deciding whether to enable this feature.

The project does not implement its own Claude OAuth login, decrypt or upload official subscription tokens, or forward official subscription requests through third-party endpoints. Users sign in and verify the result in the official client themselves. Claude Code and Claude Desktop logins are managed separately and cannot substitute for each other.

The “shared conversation pool” is a combined, deduplicated view of local transcripts and indexes. **It does not automatically create account-directory junctions or migrate every index**, and it does not make every account's native Claude sidebar identical. Actual conversation resumption must still be checked in the official client. Sharing local files cannot inherit server-side prompt caches; historical usage does not predict the next request's cost or cache hit rate.

## Getting started

1. Browse accounts and the shared pool in read-only mode first. A historical account entry is not a saved login.
2. If you choose to use experimental snapshots, sign in to the official **Claude Desktop** client and click “Capture current login” (收录当前登录). Check the identity and display name. Storage directories use internal UUIDs; a full email address can be a display name.
3. To add another account, click “Add account” (添加账号). The utility captures the current account, moves its local login files into a timestamped backup (no server-side sign-out), and reopens Claude at the sign-in page. Sign in to the other account there, then click “Capture current login”.
4. A card with a snapshot offers “Switch account” (切换账号). If Claude Desktop is running, the dialog says so; confirming quits it, preserves the current account's latest state, restores the target, and reopens Claude. Replies or Desktop-run Claude Code tasks still in progress are interrupted, so let them finish first.
5. Switch accounts with this utility, not with Claude's own “Log out”: a server-side logout may revoke the saved login. Token revocation, expiry, or client updates can also invalidate a snapshot and require a new sign-in. Verify the login in Claude after each switch.

See the [user guide (Chinese)](docs/USER_GUIDE.md) for detailed steps, provider configuration, backups, recovery, and troubleshooting.

## Data and privacy

The utility stores its preferences, request records, and backups in the installation's `data/` directory. Desktop login snapshots are stored in the current Windows user's `.claude-profiles/` directory. Authentication backups contain sensitive files, have restricted access, and are separate from ordinary conversation backups. **Do not upload runtime data or authentication backups.** Uninstall preserves data created after installation and Claude's own files. Public source and installer payloads exclude personal accounts, conversations, and login snapshots.

Third-party API keys, the WebDAV password and the cloud-backup passphrase are encrypted with Windows DPAPI in `data/` and can only be opened by the current Windows user. When you connect Claude Code or Codex to a vendor, the key is written to that application's own configuration file, as those applications require. Cloud backups are encrypted on this PC before upload, so the WebDAV server only holds ciphertext; Claude login snapshots are never uploaded.

Network activity includes inference through the provider you choose, sign-in through the official client, model detection for third-party accounts, reading official pricing pages, and the WebDAV cloud backup you configure. Basic local browsing and file management do not require a cloud service operated by this utility. Providers that need model mapping use a local relay that listens only on the loopback interface; their credentials are separate from official subscription logins.

## Development and builds

Agents taking over development should start with the [agent handoff (Chinese)](docs/AGENT_HANDOFF.md) and [AGENTS.md](AGENTS.md). They describe verified scope, real-data boundaries, implementation details, and the handoff process.

Source layout:

| Component | Path | Responsibility |
| --- | --- | --- |
| Rust core | `rust-core/` | Scanning, deduplication, usage, backups, and guarded operation dispatch |
| Local adapter | `local_adapter.py` | Snapshots, transaction records, third-party accounts and access routes, MCP/skills, pricing parsers, WebDAV cloud backup, and optional local relay |
| WPF interface | `Studio.ps1` / `Studio.xaml` | Account page and access table, shared pool, usage, backups, tray, guidance, and settings |
| EXE launcher | `launcher/` | Launching the interface without a console, instance lock, and file self-check |
| Installer | `installer/` | Chinese/English setup, shortcuts, and uninstall that preserves user data |
| Icon source | `assets/app.svg` | Original vector artwork, with PNG and multi-size ICO exports |

Build requirements are Windows PowerShell 5.1, the .NET Framework C# compiler, a Rust GNU toolchain, and Inno Setup 6.7.3. The build downloads Python 3.14.10 from Python's official site and verifies its pinned SHA-256; it does not package the developer's personal Python installation.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File packaging\build-release.ps1
```

See [development and verification notes (Chinese)](docs/DEVELOPMENT.md) for build configuration. The [documentation coordination guide](docs/DOCUMENTATION.md) describes how to maintain the English/Chinese homepages and public/local documentation. Machine-specific coordination records stay in the excluded `local-notes/` directory and are not published with source or installers.

## License and acknowledgements

New project code is licensed under [MIT](LICENSE). The historical prototype's license is retained in [UPSTREAM-LICENSE](UPSTREAM-LICENSE). Reused sources, design references, Rust dependencies, Python, and packaging tools are distinguished in [third-party notices](THIRD_PARTY_NOTICES.md) and the [open-source audit (Chinese)](docs/OPEN_SOURCE_AUDIT.md). Third-party components retain their own licenses; this project's MIT license does not replace them.

Official icons and logos from Claude, CC Switch, or other referenced projects are not bundled. Brand names describe compatibility and do not imply endorsement, partnership, or authorization.

## Contributors and AI collaboration

| Collaborator | Acknowledgement |
| --- | --- |
| GPT (OpenAI) | AI-assisted development collaborator |
| Claude (Anthropic) | AI-assisted development collaborator |

These AI acknowledgements are included at the maintainer's request; they do not independently attribute every code fragment to a particular model. The human maintainer is responsible for review, testing, merging, and release decisions. No fictitious GitHub accounts or email addresses are created for AI contributors, and these credits do not imply endorsement by OpenAI or Anthropic. See [CONTRIBUTORS.md](CONTRIBUTORS.md) for the full acknowledgement.
