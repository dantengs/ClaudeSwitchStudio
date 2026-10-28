# Documentation coordination / 文档协调说明

## Public documentation

| File | Purpose | Language |
| --- | --- | --- |
| `README.md` | Default GitHub project homepage | English |
| `README.zh-CN.md` | Chinese homepage, linked from the English page | Simplified Chinese |
| `CONTRIBUTORS.md` | Human responsibility and GPT/Claude AI acknowledgements | English and Chinese |
| `AGENTS.md` | Entry point and working boundaries for development agents | Chinese |
| `docs/AGENT_HANDOFF.md` | Implementation state, evidence, limitations, and handoff | Chinese |
| `docs/USER_GUIDE.md` / `.html` | User manual and generated offline page | Chinese |
| `docs/DEVELOPMENT.md` | Build, architecture, and validation | Chinese |
| `docs/OPEN_SOURCE_AUDIT.md` | Upstream relationships and license requirements | Chinese |
| `THIRD_PARTY_NOTICES.md` / `third-party-licenses/` | Distribution notices and original license texts | As provided |

The homepage language selector consists of ordinary relative links. GitHub renders `README.md` by default, so English remains the default without browser scripts or cookies. The Chinese page links back to English. This does not change the application's interface language or translate every supporting document.

## Keep both homepages consistent

Update both README files in the same change when modifying features, limitations, download paths, requirements, privacy statements, license references, or contributor credits. Preserve language links and relative paths. Label supporting documents that remain Chinese instead of implying that an English translation exists.

Do not put two conflicting sources of truth in the homepages. Verified implementation details belong in the handoff and development documents; contributor acknowledgements belong in `CONTRIBUTORS.md`; original upstream terms remain in their license files.

## Private coordination record

Each development machine may keep `local-notes/COORDINATION.md` **outside the distributable source files**. The main development checkout's parent workspace may hold this directory; it is not necessary to copy it into a public clone. It records actual source/build/install paths, active work, ownership, validation evidence, publication state, and the next handoff.

This directory is ignored by Git and is not among the installer payload's documentation directories. It must not be copied into `docs/`, a source ZIP, a release attachment, or an installer. Ignoring a file does not stop a custom archive command from uploading it; use explicit allowlists and inspect outputs.

Do not add private coordination data to this public page: no user account mappings, real email addresses, Windows user paths, authentication state, session contents, backup locations, or unredacted screenshots.

## Change and publication checklist

1. Read `AGENTS.md`, `docs/AGENT_HANDOFF.md`, and any private local coordination record before editing.
2. Check the working tree and agree on file ownership before concurrent edits. Put a pending item and its scope in the local record.
3. Complete the change and relevant checks. Record what passed, what remains unverified, and whether any real user data was touched.
4. Review staged files, relevant Git history, and release contents for private data. Separate the public repository identity from private account identity.
5. Publish only the intended public files and record the resulting commit/release in the local coordination file.
6. Documentation-only changes do not require replacing the existing installer or moving its published version tag. Binary updates use a new version and a fresh manifest.

GitHub account names, public profile IDs, repository ownership, and commit author metadata can be public even when application data is excluded. Use an appropriate GitHub noreply address for commits, never substitute a private email by accident, and do not describe a public repository as anonymous.

## 简体中文摘要

- `README.md` 默认英文，`README.zh-CN.md` 为中文，两页顶部互相切换。
- 修改功能、下载方式、隐私、许可或贡献者时，两份主页一起更新；其他文档暂未翻译时明确标记语言。
- GPT、Claude 的 AI 协作署名统一由 `CONTRIBUTORS.md` 说明，不伪造账号、提交邮箱或逐段代码归因。
- 本机 `local-notes/COORDINATION.md` 用于协调路径、任务分工、验证和发布记录，不能进入公开源码或安装包。
- 发布前同时检查暂存文件、相关历史和实际附件；Git 忽略规则不能代替打包白名单。
- 公开 GitHub 用户名和 noreply 提交身份依然是可见身份信息；不要把“不含私人应用数据”说成“完全匿名”。
