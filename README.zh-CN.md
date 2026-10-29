<p align="center"><img src="assets/app.png" width="104" alt="Claude Switch Studio icon"></p>
<h1 align="center">Claude Switch Studio</h1>
<p align="center">Windows 本地账号、提供商、对话与备份管理工具</p>

[English](README.md) | **[简体中文](README.zh-CN.md)**

> ❤️ 如果喜欢本项目，或者本项目对您有帮助，欢迎给本项目点个 ⭐ [Star](https://github.com/dantengs/ClaudeSwitchStudio)，感谢支持！

**独立开发、非 Anthropic 官方产品。** 当前版本面向 Windows 10/11 x64，使用 Rust 数据核心、Python 本地适配器和 WPF 界面。

## 下载与安装

从 [Releases](https://github.com/dantengs/ClaudeSwitchStudio/releases) 下载安装包，按安装向导操作：

- **0.4.0-preview（预发布版）**：`ClaudeSwitchStudio-0.4.0-preview-Setup.exe`，包含统一账号管理、第三方账号与接入表、Codex 多账号、用量统计、MCP 与技能开关、WebDAV 云备份等新功能。这是预览版，新功能只在测试环境中验证过，请先阅读 [更新说明](RELEASE_NOTES.md)。
- **0.3.1（稳定版）**：`ClaudeSwitchStudio-0.3.1-Setup.exe`。

默认安装到 `D:\programfile\Claude Switch Studio`，也可以选择其他目录。没有 D 盘时使用当前用户的程序目录。

安装后打开桌面或开始菜单中的 **Claude Switch Studio**。安装包含独立 EXE 入口、自有图标和所需 Python 运行时，无需修改系统 PATH。首次启动提供三步新手指引，设置页可以重新查看。

安装包目前未签名。请使用本仓库发布页的安装包，并与随附 `.sha256` 核对。安装器不会关闭或操作 Claude。升级前请关闭本工具窗口。

## 能做什么

- 识别本机 Claude Desktop 历史账号，展示有来源的邮箱对应关系和已收录状态。
- 以账号卡片管理多个官方 Claude Desktop 账号的实验性本地登录快照，可从官方登录页添加新账号。
- 一键切换：工具检测 Claude Desktop 是否在运行，经你确认后自动退出、创建时间戳备份、切换并重新打开 Claude；独立运行的 Claude Code 命令行不受影响。
- 对话列表跨账号双向共享：切换账号、Claude 退出期间，每个官方账号自动补上其他账号的对话（只新增，尊重删除记录）。
- 参考 CC Switch 的用量统计：可选近 24 小时、今日、7 天、30 天或全部，按账号、模型和日期（或小时）汇总 token、缓存命中与费用；Claude、Codex、Antigravity 分别按 Anthropic、OpenAI、Google 官方价格页自动更新的价格折算，趋势图为横向时间轴。
- 参考 Antigravity Tools 管理多个 Codex 账号：收录多个 ChatGPT 登录，用同样的检测-退出-重开流程切换，并显示每个账号最近的每周/5 小时额度；添加 Codex 账号在后台通过官方浏览器授权完成，不用退出 Codex；Codex 用量按账号和模型统计，并按 OpenAI 官方价格折算。
- 统一的账号管理页：Claude 官方账号、Codex 官方账号和按厂商分组的第三方账号（DeepSeek、Kimi、智谱、通义千问、MiniMax、OpenRouter、Gemini、Antigravity Tools 本地代理与自定义）。顶部接入表为 Claude Desktop、Claude Code 和 Codex 各选一个来源，修改前备份、切回时原样恢复。添加第三方账号时可用 Key 检测可用模型；Key 用 Windows 数据保护加密保存。Codex 与 CC Switch 一样以 API Key 方式接入。
- 参考 CC Switch 的 MCP 与技能开关：每个 MCP 服务和技能分别为 Claude Code、Codex 开关，支持全选 / 全不选，修改前备份、可恢复。
- 备份可保存到本机、WebDAV（坚果云、Nextcloud、群晖等）或两者；上传前在本机加密（AES-256 + HMAC-SHA256），按份数自动清理，可从云端恢复到本机，启动时提示其他电脑上传的新备份。
- 托盘常驻：点 × 隐藏窗口，托盘菜单可切换账号、打开 Claude、开机自启或退出。备份可命名、重命名，删除时移到回收站。
- 可从已安装的 CC Switch 只读导入 Claude 与 Codex 提供商（同一个 Key 合并为一个账号）；不会修改或捆绑 CC Switch。
- 汇总官方版、第三方版的本地会话索引，按原文身份去重，提供搜索与历史用量查看。
- 查看本地项目记忆，备份会话原文、索引与相关状态。
- 深浅主题、可调字号、统一矢量侧栏图标、首次启动指引。

## 重要边界

**登录快照是实验功能，不是官方授权接口。** 工具只检查本地文件和缓存，不能保证登录有效或没有账号风险。Anthropic 的规则限制第三方应用提供 Claude.ai 登录、收集或保存会话令牌及代用户转发订阅请求；本地备份并不等于获得官方许可。请阅读 [官方凭证使用说明](https://code.claude.com/docs/en/legal-and-compliance#authentication-and-credential-use)，自行评估是否启用。

本项目没有自建 Claude OAuth 登录，不解密或上传官方订阅令牌，不通过第三方端点转发官方订阅请求。用户自行在官方客户端登录、检查登录有效性。Claude Code 登录与 Claude Desktop 登录分别管理，不能互相替代。

“共享对话池”是对本机原文和索引的统一、去重视图。**没有自动创建账号目录联接或自动迁移全部索引**，也不会让每个账号的 Claude 原生侧栏自动完全相同。恢复对话的实际行为仍需官方客户端确认。共享本地文件不能继承服务端提示词缓存，历史用量不代表下一次请求的费用或缓存命中率。

## 开始使用

1. 先只读查看账号和共享池；历史账号记录不等于已保存登录。
2. 如决定使用实验性快照，在官方 **Claude Desktop** 登录当前账号，点击“收录当前登录”，确认身份与显示名称。目录使用内部 UUID，完整邮箱可以用作显示名称。
3. 添加其他账号时点“添加账号”：工具先收录当前账号，把本地登录文件移入时间戳备份（不在服务器上注销），再让 Claude 打开登录页。登录新账号后点“收录当前登录”。
4. 有快照的卡片显示“切换账号”。Claude Desktop 在运行时，确认窗口会说明；确认后工具自动退出 Claude，保留当前账号的最新状态，恢复目标快照并重新打开 Claude。进行中的回复和 Desktop 内的 Claude Code 任务会被中断，请先等它们结束。
5. 换号请使用本工具，不要用 Claude 自带的“退出登录”：服务器端注销可能使已保存的登录失效。令牌撤销、过期或客户端更新也可能使快照失效，需要重新登录。每次切换后请在 Claude 中确认登录。

完整步骤、第三方配置、备份恢复和常见问题见 [使用手册](docs/USER_GUIDE.md)。

## 数据与隐私

程序自己的偏好、请求记录和备份保存在安装目录的 `data/`；桌面版登录快照保存在当前 Windows 用户的 `.claude-profiles/`。认证备份包含敏感文件，限制访问权限并与普通会话备份分开。**不要上传运行数据或认证备份。** 卸载不会删除运行后产生的 `data/` 或 Claude 自己的数据；公开源码及安装包不包含任何用户账号、对话或快照。

第三方账号的 API Key、WebDAV 密码和云备份加密密码用 Windows 数据保护（DPAPI）加密保存在 `data/`，只有当前 Windows 用户能解开；接入 Claude Code 或 Codex 时，Key 会按这些应用的要求写入它们自己的配置文件。云备份文件在本机加密后才上传，WebDAV 服务器只保存密文；Claude 登录快照不会上传。

需要网络的操作包括用户选择的提供商推理、通过官方客户端登录、检测第三方模型列表、读取官方价格页，以及你配置的 WebDAV 云备份。基础本地浏览和文件管理不需要工具自己的云端服务。对于有模型映射的第三方提供商，工具会使用只监听回环地址的本地转发器；其凭证与官方订阅登录分离。

## 开发与构建

后续智能体请先阅读 [开发交接文档](docs/AGENT_HANDOFF.md) 和 [AGENTS.md](AGENTS.md)，其中记录已验证范围、真实数据边界、关键实现与接手步骤。

源码入口：

| 部分 | 路径 | 职责 |
| --- | --- | --- |
| Rust 核心 | `rust-core/` | 扫描、去重、用量、备份、受保护的操作分发 |
| 本地适配器 | `local_adapter.py` | 快照、事务记录、第三方账号与接入、MCP/技能、价格解析、WebDAV 云备份与可选本地转发 |
| WPF 界面 | `Studio.ps1` / `Studio.xaml` | 账号管理与接入表、共享池、用量、备份、托盘、引导与设置 |
| EXE 入口 | `launcher/` | 隐藏控制台启动界面、实例锁和文件自检 |
| 安装器 | `installer/` | 中文/英文安装、快捷方式、保留数据的卸载 |
| 图标源文件 | `assets/app.svg` | 原创矢量图标，附 PNG 和多尺寸 ICO |

构建依赖：Windows 自带 PowerShell 5.1、.NET Framework C# 编译器，Rust GNU 工具链，以及 Inno Setup 6.7.3。运行版 Python 3.14.10 从 Python 官网下载，构建脚本核对固定 SHA-256，不使用开发者个人 Python 打包。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File packaging\build-release.ps1
```

构建配置与验收方式见 [构建和架构说明](docs/DEVELOPMENT.md)。中英主页及公开/本地文档的维护约定见 [文档协调说明](docs/DOCUMENTATION.md)；本机协调记录保存在被排除的 `local-notes/`，不随源码或安装包发布。

## 许可与致谢

本项目新增代码采用 [MIT](LICENSE)。历史原型的许可保存在 [UPSTREAM-LICENSE](UPSTREAM-LICENSE)。已复用的来源、纯设计参考、Rust 依赖、Python 与打包工具分别列在 [第三方声明](THIRD_PARTY_NOTICES.md) 和 [开源审计说明](docs/OPEN_SOURCE_AUDIT.md)。第三方组件保持各自许可，不因本项目使用 MIT 而改变。

不附带 Claude、CC Switch 或其他参考项目的商标和官方图标；品牌名称仅用于描述兼容对象，不表示得到官方认可或合作授权。

## 贡献者与 AI 协作

| 协作者 | 说明 |
| --- | --- |
| GPT（OpenAI） | AI 辅助开发协作者 |
| Claude（Anthropic） | AI 辅助开发协作者 |

上述 AI 协作署名按维护者要求列出，不代表已独立核实每一段代码的模型来源。人类维护者负责审查、测试、合并与发布决定；不为 AI 创建虚构的 GitHub 账号或邮箱，也不表示 OpenAI 或 Anthropic 对本项目的背书。完整说明见 [贡献者说明](CONTRIBUTORS.md)。
