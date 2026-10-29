# Security and private data

Do not publish authentication profiles, `.credentials.json`, Desktop storage, application `data/`, private backups, conversation JSONL, account mappings, request logs or screenshots containing personal information.

The Desktop login snapshot feature is experimental and unsupported by Anthropic. Local file presence and hashes do not validate a remote login, authorization, compliance or freedom from account restrictions. Never share account tokens or use them with third-party inference endpoints.

Do not report vulnerabilities by attaching real credentials or conversations to a public issue. Use synthetic fixtures and redact identifiers. Before testing authentication changes on a real installation, obtain the user's confirmation, ask them to fully quit Claude, and create a timestamped backup. This program must not stop Claude without the user's confirmation in its exit dialog, must not close independent Claude Code sessions, and must not silently migrate personal data.

Third-party API keys, the WebDAV password and the cloud-backup passphrase are stored with Windows DPAPI for the current user; request files that carried them are removed after use. Cloud backups are encrypted locally before upload. Never put real keys, WebDAV credentials or backup passphrases in issues, tests or screenshots.

Uninstall intentionally preserves runtime data and backups. Users should handle credential revocation through the official client and explicitly review any later local cleanup.
