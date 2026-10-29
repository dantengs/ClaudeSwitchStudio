# Claude Switch Studio 开发交接

更新日期：2026-10-28，北京时间。本文面向后续开发智能体及维护者，记录本次开发的实现、验证证据和未完成边界。本文不构成执行真实账号操作的授权。

## 1. 接手后的第一步

1. 以当前 GitHub 仓库的检出目录为源码入口，先查看 `git status`，保留已有修改。
2. 阅读本文、`AGENTS.md`、`README.md`、`docs/DEVELOPMENT.md`、`docs/USER_GUIDE.md`、`docs/OPEN_SOURCE_AUDIT.md`。
3. 区分源码、安装目录和用户运行数据。不要在安装目录直接开发，也不要把历史工作副本的私密数据同步到仓库。
4. 先运行与改动有关的隔离测试。没有新改动、失败或未解决疑点时，不需要重复所有既有验收。
5. 用户可能仍在 Claude 中工作。只读查看与源码工作可继续；需要真实文件变更时先提醒、按当前授权处理退出与时间戳备份。

## 2. 当前发布基线

| 项目 | 基线 |
| --- | --- |
| 仓库 | <https://github.com/dantengs/ClaudeSwitchStudio> |
| 发布版本 | `v0.3.1`，Windows 10/11 x64 安装版 |
| 发布页 | <https://github.com/dantengs/ClaudeSwitchStudio/releases/tag/v0.3.1> |
| 功能发布提交 | `8e7a7d91460291dd0e30d1a55ceb3f1063c4fc67` |
| 安装文件 | `ClaudeSwitchStudio-0.3.1-Setup.exe` |
| 安装包 SHA-256 | `1cb04592d4c2c99463741722e9112de38d09312e95d0dad9d20d91efc412e997` |
| 安装载荷 | 129 个文件，发布页附相对路径和 SHA-256 清单 |
| 签名 | 尚无产品代码签名证书 |

上表是已发布二进制的固定基线；交接文档可以在后续文档提交中继续更新，不应因此移动旧发布标签。将来发新版时同步更新版本、验证和发布记录。

## 3. 产品目的与用户偏好

工具管理本机 Claude Desktop 官方账号的实验性登录快照、第三方提供商、本地对话索引、项目记忆位置与备份。用户希望减少重复复制会话，提高账号识别、切换和阅读体验。

已确认的交互原则：中文优先；深浅主题和可调字号；侧栏图标尺寸、文字起点严格一致；首次启动有新手指引。账号、共享对话池、历史缓存/用量、备份和设置分别展示。

用户要求在迁移数据或需要退出 Claude 时提前提醒，保留原账号和目标账号已有数据，操作前按时间备份。用户自己在官方客户端登录并检查实际可用性。此前明确不希望开发助手操作 Claude 界面。

## 4. 已实现与未实现

| 能力 | 当前状态 |
| --- | --- |
| 账号与邮箱 | 从本地记录精确匹配 UUID；支持用户确认对应关系及自定义显示名，注明来源 |
| 账号卡片 | 双列卡片，区分“添加登录”“收录当前登录”“更新登录状态”“切换账号”，显示收录时间 |
| 添加账号（未发布） | `login-new`：先收录当前账号，移走本地登录文件（不服务器注销），让 Claude 打开登录页 |
| 退出与重启（未发布） | `claude-processes` 只读检测；用户确认后 `QuitClaude` 退出 Desktop 进程树，`RelaunchClaude` 经 explorer 重新打开 |
| 本地快照 | UUID 内部目录、身份核对、缓存存在检查、ACL、备份、复制校验、事务记录 |
| 官方/第三方切换 | 本地文件适配器已实现；切出官方模式时保留当前最新状态 |
| 第三方发现 | 只读识别用户已安装 CC Switch 的桌面提供商配置 |
| 共享池 | 汇总本地索引，按原文身份去重，保留分支；不是物理迁移 |
| 记忆/用量 | 查看本地记忆位置及文件数，读取会话里已有的历史用量 |
| UI | WPF 卡片、主题、15/17/19 字号、矢量侧栏、三步首次引导和设置重看 |
| 分发 | 带图标 EXE 入口、内置 Python、中文/英文安装、数据保留式卸载 |
| 自建 OAuth | 未实现；没有将 CLI 授权冒充 Desktop 登录 |
| 物理共享目录 | 未对真实账号目录创建 Junction 或 Symlink |
| 原生侧栏完全同步 | 未实现，不能声称每个账号在 Claude 内看到同一份全部会话列表 |
| 自动换号/预热 | 未实现按额度自动换号，不发送模型预热请求；切换始终由用户点击发起 |
| 服务端缓存继承 | 不提供，本地原文共享无法保证服务端提示词缓存命中 |
| 在线登录有效性 | 未由本工具验证，不能以本地文件检查代替 |

## 5. 架构与代码入口

启动链为 `ClaudeSwitchStudio.exe → Windows PowerShell 5.1/WPF → Rust JSON IPC → 必要时调用 Python`。

| 文件 | 重点入口 | 职责 |
| --- | --- | --- |
| `launcher/Program.cs` | `Main`、`SelfCheck` | 隐藏控制台、等待 UI 子进程、持有应用实例锁、只读文件自检 |
| `Studio.ps1` | `Invoke-Job`、`Update-State`、`Show-Save`、`Show-LoginGuide` | 异步请求、卡片状态、收录引导和错误回调 |
| `Studio.ps1` | `Show-Onboarding`、`Complete-Onboarding`、`Save-Preferences` | 首次指引与偏好合并持久化 |
| `Studio.xaml` | Nav 样式、账号/提供商模板、Onboarding 弹层 | UI 布局及矢量图标 |
| `studio-core.ps1` | `Read-Json`、`Write-Json`、`Prop` | 最小文件和对象辅助函数，不是旧版认证内核 |
| `rust-core/src/main.rs` | `Context::new/profiles/state_base` | 目录探测、快照完整性与基础账号状态 |
| `rust-core/src/main.rs` | `backup/copy/undo`、`run` | 会话备份、受限索引操作、IPC 与维护门禁 |
| `rust-core/src/workspace.rs` | `enrich`、`pool_rows`、`sharing_plan` | 邮箱/别名、第三方工作区、去重池、只读共享计划 |
| `rust-core/src/workspace.rs` | `python_path`、`provider_catalog` | 优先选内置运行时，并调用公开提供商目录接口 |
| `local_adapter.py` | `save_account`、`switch_account`、`switch_provider` | 真实认证/配置文件的受保护变更 |
| `local_adapter.py` | `snapshot`、`auth_operation`、`transaction_state`、`reject_pending` | 备份、共享认证锁、跨安装中断检测 |
| `local_adapter.py` | `relay_handler`、`start_relay` | 第三方模型映射所需的可选回环转发器 |
| `packaging/` | `build-release.ps1`、`collect-licenses.ps1` | 运行时校验、载荷组装、许可收集 |
| `installer/` | `Studio.iss`、`build-installer.ps1`、`verify-installer.ps1` | 构建安装器及隔离安装验收 |

这是混合实现，不是 Tauri 应用，也不是纯 Rust GUI。EXE 入口不会把 PowerShell/WPF 改写成 Rust，只负责正常的桌面启动体验。

## 6. 数据位置与边界

使用当前 Windows 用户路径探测，不要写死开发者姓名或真实账号 UUID。

| 数据 | 位置或规律 |
| --- | --- |
| 官方 Desktop 首选数据根 | `%LOCALAPPDATA%\Claude-Data\claude_pzs8sxrjxfjjc`，并兼容代码列出的旧位置 |
| 第三方版根 | `%LOCALAPPDATA%\Claude-3p` |
| 会话原文和项目记忆 | `%USERPROFILE%\.claude\projects\<项目编码>\`，包含 JSONL 和 `memory/` |
| Desktop 账号索引 | 数据根下 `claude-code-sessions\<账号>\<组织>\local_*.json` |
| 登录快照 | `%USERPROFILE%\.claude-profiles\desktop-<账号 UUID>`；已有旧目录保持兼容 |
| 工具运行数据 | 程序目录 `data/`，包括偏好、请求记录、普通备份与私密认证备份 |
| 第三方配置来源 | `%USERPROFILE%\.cc-switch\cc-switch.db`，只读查询相关提供商 |
| CLI 认证 | `%USERPROFILE%\.claude\.credentials.json` 等；不要当作 Desktop 登录来源来恢复 |

显示邮箱和内部目录 ID 分离。`identities.json` 中明确由用户确认的邮箱可以覆盖展示，但不得据此伪造或重写官方 OAuth 身份。`HasCustomName` 保证显式别名不被邮箱刷新覆盖，`EmailDetail` 独立展示邮箱来源。

共享池的身份包含 CLI 会话 ID 与原文路径，分支不能因标题或应用会话 ID 相同而合并。`OriginKeys/Origins` 保留来源，未注册原文单独分类。

不要整个链接账号索引目录：目录中还可能有计划任务、删除标记、桥接状态和账号专属文件。现有 `workspace-plan` 明确只读，`PhysicalLinkEnabled=false`。

## 7. IPC 与操作保护

Rust 接口通过 `--request <JSON> --response <JSON> --base <程序根>` 调用，结果为 `{"Ok":true,"Result":...}` 或 `{"Ok":false,"Error":...}`。

| Action | 性质 |
| --- | --- |
| `state`、`usage`、`workspace-plan`、`claude-processes` | 业务只读；仍可能创建本工具的请求/锁文件 |
| `label` | 仅修改本工具的显示标签 |
| `backup` | 要求退出 Claude，创建普通会话备份 |
| `copy`、`undo` | 有维护确认和校验保护的历史数据操作；不是默认共享方案 |
| `save`、`switch`、`provider-switch`、`login-new` | 真实认证/配置变更，必须有维护确认和进程检查 |

`MaintenanceConfirmed=true` 只是程序门禁，不等于智能体已经得到用户许可。文档中的接口说明也不构成运行真实操作的指令。

### 进程识别、退出与重启

进程检查统一在 `local_adapter.py`（`claude_processes`、`ensure_closed`），Rust 对认证动作不再重复做按名称的检查。只看本 Windows 会话中的 `claude.exe`：

- `desktop`：路径含 `\WindowsApps\Claude_` 或 `\AnthropicClaude\`，包括 Electron 子进程；
- `agent`：祖先是 desktop 进程的 Claude Code（Desktop 内的会话）；
- `cli`：其他路径的独立 Claude Code，不读写 Desktop 登录文件，不阻止操作、永不关闭——即使它运行的是 Desktop 自带的同一个二进制；
- `unknown`：读不到路径，阻止操作且不自动关闭。

`execute()` 顺序：认证锁 → `reject_pending` → `preflight`（只读校验快照/身份/提供商，失败时不关闭 Claude）→ 仅当 `QuitClaude=true` 才 `quit_desktop`（`taskkill /T` 约 4 秒宽限，Desktop 通常隐藏到托盘，随后 `/T /F`；每轮重新读取进程表以免误杀复用 PID；退出后等待 1.5 秒释放 LevelDB/SQLite 句柄）→ 执行动作 → `RelaunchClaude` 时经 `explorer.exe shell:AppsFolder\<AUMID>` 或 exe 路径打开，Claude 不成为本工具子进程。动作失败时只在所有事务记录为终态（原登录保留或已还原）才重新打开；`rollback-needs-review` 时保持关闭。

`login-new` 把 `Network`、`Local Storage`、`Session Storage`、`config.json` 移到数据目录内的 `.studio-displaced-*`，写回去掉 `oauth:*`、`lastKnownAccountUuid`、`bootSideSessions` 的 `config.json`，保留 `Local State` 与其他设置。“这样足以让 Desktop 显示登录页”是基于文件结构的推断，**尚未在真实客户端验证**。

### 对话列表同步与托盘

`sync_conversations`（`local_adapter.py`）在 Claude 关闭时运行：读取每个官方账号各组织目录里的 `local_<uuid>.json`，按 `sessionId` 取 `lastActivityAt` 最新的一份，向每个账号“已有对话的组织目录”（没有时用全部组织目录）补上缺少的条目。只用 `open('x')` 新建，跳过已存在文件、同目录 `deleted_<uuid>` 标记和本机没有原文的条目；去掉 `scheduledTaskId`、`remoteMcpServersConfig`，清空 `bridgeSessionIds`；新建文件记入 `data/conversation-sync/<时间戳>.json`。已有索引的标题、活动时间不会互相更新；第三方根目录不参与；尚无撤回界面（日志里有路径与 SHA-256）。请求带 `SyncConversations=true` 时，`execute()` 在动作成功后、重新打开 Claude 之前同步；也可单独发 `sync-conversations`。

界面用 `Dispatcher.Run()` 代替 `ShowDialog()`，× 在 `CloseToTray` 开启时只隐藏窗口，`Exit-Studio` 才真正退出。`SetCurrentProcessExplicitAppUserModelID('ClaudeSwitchStudio.App')` 与安装器快捷方式的 `AppUserModelID` 必须一致。启动器第二次运行时 `Set()` 命名事件 `ClaudeSwitchStudio.Show`，界面定时器收到后唤出窗口；`--tray` 供 HKCU `Run` 项开机自启，卸载时 `[Code]` 删除该值。备份新增 `Name`、`backup-rename`、`backup-delete`（移到回收站）。

### Codex 账号、Codex 用量与 MCP/技能开关

进程辅助函数按 `APPS` 区分 claude 与 codex（`claude_processes(fixture, app)` 等保持原签名默认值）。Codex 桌面版是 `WindowsApps\OpenAI.Codex_*\app\ChatGPT.exe`，其子进程 `codex.exe` 归为 agent；独立 `codex.exe` 归为 cli，并且**阻止**切换（`CliBlocks`），因为它共用 `~/.codex/auth.json` 并可能在刷新令牌时改写。快照在 `~/.codex-profiles/<account_id>/`（ACL 保护），身份来自 `tokens.account_id` 与本地解码的 id_token（仅用于显示）。`codex-save/switch/login-new` 走与 Claude 相同的 `execute()` 流程（锁、预检、退出、重开）；切换前先 checkpoint 当前账号，同账号只更新快照。额度取每个账号最新会话（`session_meta.creator_account_id`）里的 `token_count.rate_limits`。`usage-stats` 带 `App=codex` 时解析 `~/.codex/sessions` 与 `archived_sessions`：`token_usage_record` 按 `response_id|turn_id` 去重，旧会话回退到 `token_count` 的增量；OpenAI `input_tokens` 含缓存读取，拆为未缓存输入与缓存读取；不计费。`Hours` 参数为滚动窗口与每小时趋势。

Codex 添加账号（`codex-oauth-start/status/cancel`）在 `data/codex-oauth/<id>/home` 作为 `CODEX_HOME` 运行官方 `codex login`（优先 Codex 桌面版自带的 `%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe`），界面每 2 秒查询，出现已登录的 `auth.json` 即复制到 `~/.codex-profiles/<account>/` 并删除临时目录；结束登录进程前核对 PID 对应的可执行文件。`codex-save` 与这些动作属于 `CONFIG_ACTIONS`，不要求退出 Codex。OpenAI 价格由 `parse_openai_pricing` 解析官方 markdown（以单独的 Standard/Batch/Flex/Fast 行区分档位），写入 `data/pricing-openai.json`；`openai_cost` 把 `input_tokens` 拆为未缓存、缓存读与写入，超过 272K 用 LongPrompt。Claude Desktop 没有可指定目录的官方登录命令，不做后台授权。

第三方账号与接入表：`vendor_catalog`（`vendor-state`）输出厂商预设 `VENDORS`、账号（不含 Key）和三个应用的当前来源。账号存 `data/vendors.json`，Key 经 `dpapi()`（CryptProtectData，当前用户）加密；Antigravity Tools 预设可设 `LinkedKey`，接入时才读其代理密钥。`vendor-save/vendor-delete/vendor-models/vendor-import/route-claude-code` 属于 CONFIG_ACTIONS；`codex-route` 按 codex 应用走退出/重开流程，Claude Desktop 用 `provider-switch` 的 `vendor:<Id>`。接入前的值存 `data/routes.json` 的 `Previous` 用于精确恢复；旧版 `data/agy-connections.json` 由 `route_state` 视为 `antigravity-legacy` 来源，切回官方时恢复并清除。Codex 第三方提供方固定为 `[model_providers.studio]`（Responses 接口），`set_top_key` 原位替换以便恢复后文件不变。主程序在 `execute` 前把请求文件中的 `Key` 删除，界面在读取响应后删除请求与响应文件。账号的 `Codex` 字段是 Responses 地址（旧版的 `Responses` 布尔值在读取时迁移）；Codex 提供方带 `requires_openai_auth = false` 和 `experimental_bearer_token`，不改 auth.json。本工具不做号池或请求转发，也不修改 Antigravity Tools 的设置。云备份：`data/cloud.json`（密码与加密密码经 DPAPI），`cloud-save/list/upload/restore/check`；文件格式 `CSSB1\n`+salt+iv+AES-256-CBC（Windows CNG，`AesCbc`）+HMAC-SHA256，PBKDF2-SHA256 30 万次；远程名 `settings|session-时间-机器标签[-备份ID].cssb`，按机器与种类保留 Keep 份。临时文件在 `data/cloud-temp/` 并在结束时删除。用量 `agy-usage` 读 `token_stats.db` 的 `token_usage`（`input_tokens` 含缓存），Google 价格 `parse_google_pricing` 解析官方 HTML 的 Standard 段落，带日期区间与 200K 档位，`*-preview` 另存去后缀别名，相邻的 `-customtools` 共用价格。本工具不实现自己的 Google 代理，也不复制 Google 令牌。

`mcp-toggle` 编辑 `~/.claude.json` 的 `mcpServers` 或 `config.toml` 的 `[mcp_servers.<name>]`：TOML 文本只删改该表，前后用 `tomllib` 解析比对其余内容完全一致才写入；关闭时原配置存 `data/mcp-library.json`。`skill-toggle` 在 `~/.claude/skills` 与 `~/.codex/skills` 之间复制（树哈希校验），关闭前在 `data/skills-library` 和时间戳备份各存一份才 `rmtree`。两者走 `CONFIG_ACTIONS`，不退出应用。与 CC Switch 同时管理会互相覆盖，未做互通。

开发机上本智能体本身就运行在 Claude Desktop 中：任何真实 `QuitClaude` 都会结束当前会话。测试只用 `--fixture` 的合成 `processes.json`/`process-log.json`；不要为验收在开发会话中调用真实退出。

`save` 使用 `ExpectedAccount` 防止陈旧卡片收录错误身份，`DisplayName` 只用于显示。`switch` 同时核对目标快照与预期账号。发现目标其实就是当前身份时，更新当前快照而不是把旧令牌恢复到当前文件上。

三种锁不能混为一谈：

- C# 的 `ClaudeSwitchStudio.Runtime` 是本工具实例锁，供安装器识别。
- Rust 的 `data/operations.lock` 是本安装目录的数据操作锁。
- Python 的 `.claude-profiles/.studio-auth.lock` 是多个新版安装共用的认证锁。

旧工具不认识新增公共锁；仅凭“另一个版本也有 Busy 状态”不能证明认证变更互斥。

认证事务同时记在私密备份及 `.claude-profiles/.studio-transactions/`。只有 `committed` 或 `rolled-back` 被视为结束。若发现未完成记录，后续认证操作应停止并核对备份；当前没有完整的图形化恢复向导，不能删除 journal 强行继续。

## 8. 已验证的范围

版本发布前已通过：

- 20 项 Python 隔离用例：身份拒绝、快照、最新令牌保留、同账号防旧状态覆盖、跨卷归档、失败恢复、共享锁、中断检测、样例 Junction 和模拟转发器。
- 8 项 Rust 用例：ID、账号动作、邮箱与别名、去重/分支、历史用量、备份/受限撤销、运行时选择等。
- 偏好合并测试：保留未知字段和引导版本；只读/测试模式不持久化。
- WPF 自身只读渲染测试：六项侧栏文字起点一致，三步指引导航，账号及提供商确认后的只读拒绝。
- 安装、重装、卸载样例：129 个载荷文件 hash 一致，重装与卸载保留运行后创建的样例 `data/`。
- GitHub Windows fixture workflow 在功能发布提交上通过；后续状态应查看 Actions，不把旧结果当作新改动的证据。

开发期还做过单个当前账号的真实本地快照收录及源文件/快照一致性核对，没有打开 Claude 或发送模型请求。另一个账号尚需用户自行登录后收录；没有完成两个官方账号的真实往返恢复及续聊验证。早期版本的第三方配置文件往返测试不等于本版对全部提供商的在线推理验证。

真实验收记录、账号映射和截图留在本机，没有放入公开仓库。接手者不要为了补证据擅自重做真实认证操作。

## 9. 测试与构建

最小测试入口：

```powershell
python -m unittest discover -s tests -v
cargo test --locked --manifest-path rust-core/Cargo.toml
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\test-ui-preferences.ps1
```

Python 测试需要 Python 3.11+ 的相关标准库能力；发行版固定内置 Python 3.14.10。不同盘符测试可能在单盘环境跳过，不能把跳过称为验证通过。

构建前读 `docs/DEVELOPMENT.md`，确认 Rust GNU 工具链布局与 Inno 路径。命令入口：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File packaging\build-release.ps1
```

注意：默认工具链目录是本次开发的隔离布局，不是所有机器通用路径。现有 `packaging/payload` 或同名发行产物会导致构建停止；选择新工作副本或先明确归档旧产物，不递归删除来源不明目录。即使选择 `-SkipCompile`，当前脚本的许可收集仍需要能解析 Cargo 元数据的工具链，不能理解成“完全不需要 Rust”。

发布版本号目前分布于 Cargo、C# assembly/manifest、Inno、打包脚本和部分 UI/文档中，尚未统一成单一版本源。升级前逐处核对，避免只改安装器版本。

安装器固定 AppId 为 `{6115D5A1-F07B-49F4-B41D-FCB71896493A}`。**更换 `/DIR` 不能隔离卸载注册和快捷方式。** 当前机器若已有正式安装，不运行样例卸载脚本；使用干净虚拟机或独立测试用户。不得为绕过保护而删除正式注册表键。

## 10. 本次踩过的坑

| 问题 | 原因及已采取的处理 |
| --- | --- |
| 切换按钮一直灰 | 将历史账号误当作已有登录；改为按状态显示添加、收录、更新和切换动作 |
| 邮箱无法作为名称 | 显示名被当作受限目录名；内部改用 UUID，显示名独立 |
| 命名成功但刷新消失 | 邮箱覆盖 Name；保留显式别名，邮箱另列 |
| 跨盘恢复失败 | 把 `os.replace` 当成跨卷移动；目录在原卷暂存，跨卷归档使用复制、hash 校验后处理自有源文件 |
| 保存中断后旧快照消失 | 两次重命名间没有持久记录；增加全局可见事务，检测未完成操作 |
| 两版本并发操作 | 每安装目录锁不共享；增加用户级认证锁和旧版并发提醒 |
| 令牌回退为旧值 | 切出前不更新当前快照；增加 outgoing checkpoint，同账号也不恢复旧状态 |
| 侧栏符号错位 | Unicode 字形尺寸不同、内容整体居中；改为固定图标列与矢量 Path，内容拉伸 |
| 中文脚本/文件替换异常 | 产品固定 PS5.1；保留 UTF-8 BOM，`File.Replace` 空备份参数使用 `[NullString]::Value` |
| 动态模板按钮无响应 | RoutedEvent 要辨认 `OriginalSource`，闭包需正确捕获账号对象和错误回调 |
| 打包 Python `-c` 引号失效 | PS5 的本机参数传递与 PS7 不同；避免依赖内层双引号的简单自检，或使用保存的脚本 |
| Cargo 许可离线解析失败 | 无目标过滤触发其他目标的未缓存包；使用 `--filter-platform x86_64-pc-windows-gnu` |
| GitHub 显示未知许可 | 项目 LICENSE 混入额外说明；标准 MIT 正文独立，第三方说明另存 |

`studio-core.ps1` 中旧 `Check-Profile` 辅助函数仍保留，但新的收录流程不再依赖它验证显示邮箱。不要把旧函数重新接回显示名流程。

## 11. 已知限制与后续建议

以下是建议方向，不是本次用户已授权执行的待办：

1. **先完善恢复与诊断。** 做只读事务诊断页，展示涉及文件、验证结果与可审核的恢复计划；再考虑用户确认后的恢复执行。
2. **补真实场景验证。** 由用户手动完成另一账号登录/收录，约定维护窗口后验证跨账号恢复、令牌撤销/过期及 Desktop 更新行为。先证明流程，再承诺“一键切换”。
3. **改善数据目录设计。** 当前工具数据在安装目录，旧版备份分散。可评估独立数据根与迁移计划，但不得默认移动私密备份或已有账号数据。
4. **增强构建复现。** 统一版本源、改进标准 Rust 安装适配、加入干净环境的安装器验收；实际执行再核对许可证与运行时来源。
5. **评估原生化。** 若做 Tauri/其他 Rust GUI，先保留现有 IPC 和数据保护语义；不要把重写 UI 当成改写认证协议的许可。
6. **审慎研究共享目录。** 先区分可共享原文与账号专属状态，做样例测试和可逆计划；没有真机证据前，不自动链接完整账号目录。
7. **保持 OAuth 边界。** 当前没有 Desktop 官方公开多账号授权集成；CLI 官方目录隔离和登录机制不能直接等同于 Desktop。未来接入前重新核对官方接口与规则。

## 12. 许可与公开发布

新增源码为 MIT；保留历史 Sevin 原型 `UPSTREAM-LICENSE`。CC Switch、ai-multi-instance、mysqto/ccswitch 等关系在 `docs/OPEN_SOURCE_AUDIT.md` 区分为原型、设计参考或互操作，不得混写成捆绑内核或官方合作。

实际发行附件包含 27 个第三方 Cargo 包的许可材料、Rust 库/工具链版权及相关许可、Python 官方许可和组件说明、OpenSSL 实际存在的声明文件、Inno 许可。`THIRD_PARTY_NOTICES.md` 和 `third-party-licenses/` 需要随依赖变化更新。

安装器与源代码都必须排除个人数据。尤其不要上传 `data/`、`.claude*`、私密备份、JSONL、账号映射、真实请求/响应和含邮箱的截图。`.gitignore` 是第二道保护，不能代替白名单与内容检查；被忽略文件仍可能被错误打进 ZIP 或安装载荷。

## 13. 可直接交给下一位智能体的任务说明

> 请接手 Claude Switch Studio。先读 AGENTS.md 和 docs/AGENT_HANDOFF.md，查看工作区状态及当前源码，不覆盖已有改动。以 v0.3.1 的已发布架构和数据保护为基线，先用隔离样例验证你要修改的路径。用户可能仍在 Claude 中工作；不要启动、关闭或替用户登录 Claude，不执行真实快照恢复、提供商切换或数据迁移，除非获得对应授权并先提醒退出、创建时间戳备份。共享池当前只是本地去重视图，Desktop 登录仍是实验功能。交付时写清改动、实际测试范围、未验证事项和许可影响，不提交任何个人运行数据。
