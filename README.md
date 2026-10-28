<p align="center"><img src="assets/app.png" width="104" alt="Claude Switch Studio icon"></p>
<h1 align="center">Claude Switch Studio</h1>
<p align="center">Windows 本地账号、提供商、对话与备份管理工具</p>

**独立开发、非 Anthropic 官方产品。** 当前版本面向 Windows 10/11 x64，使用 Rust 数据核心、Python 本地适配器和 WPF 界面。

## 下载与安装

从 [Releases](https://github.com/dantengs/ClaudeSwitchStudio/releases) 下载 `ClaudeSwitchStudio-0.3.1-Setup.exe`，按安装向导操作。默认安装到 `D:\programfile\Claude Switch Studio`，也可以选择其他目录。没有 D 盘时使用当前用户的程序目录。

安装后打开桌面或开始菜单中的 **Claude Switch Studio**。安装包含独立 EXE 入口、自有图标和所需 Python 运行时，无需修改系统 PATH。首次启动提供三步新手指引，设置页可以重新查看。

安装包目前未签名。请使用本仓库发布页的安装包，并与随附 `.sha256` 核对。安装器不会关闭或操作 Claude。升级前请关闭本工具窗口。

## 能做什么

- 识别本机 Claude Desktop 历史账号，展示有来源的邮箱对应关系和已收录状态。
- 收录、更新及恢复官方桌面版的本地登录快照；操作前提示退出 Claude，创建时间戳备份。
- 从已安装的 CC Switch 配置只读识别第三方提供商，并管理本机第三方模式；不会捆绑 CC Switch。
- 汇总官方版、第三方版的本地会话索引，按原文身份去重，提供搜索与历史用量查看。
- 查看本地项目记忆，备份会话原文、索引与相关状态。
- 深浅主题、可调字号、统一矢量侧栏图标、首次启动指引。

## 重要边界

**登录快照是实验功能，不是官方授权接口。** 工具只检查本地文件和缓存，不能保证登录有效或没有账号风险。Anthropic 的规则限制第三方应用提供 Claude.ai 登录、收集或保存会话令牌及代用户转发订阅请求；本地备份并不等于获得官方许可。请阅读 [官方凭证使用说明](https://code.claude.com/docs/en/legal-and-compliance#authentication-and-credential-use)，自行评估是否启用。

本项目没有自建 Claude OAuth 登录，不解密或上传官方订阅令牌，不通过第三方端点转发官方订阅请求。用户自行在官方客户端登录、检查登录有效性。Claude Code 登录与 Claude Desktop 登录分别管理，不能互相替代。

“共享对话池”是对本机原文和索引的统一、去重视图。**没有自动创建账号目录联接或自动迁移全部索引**，也不会让每个账号的 Claude 原生侧栏自动完全相同。恢复对话的实际行为仍需官方客户端确认。共享本地文件不能继承服务端提示词缓存，历史用量不代表下一次请求的费用或缓存命中率。

## 开始使用

1. 先只读查看账号和共享池；历史账号记录不等于已保存登录。
2. 如决定使用实验性快照，在官方 **Claude Desktop** 登录当前账号，完成工作并完全退出 Desktop、Claude Code 和第三方版。
3. 点击“收录当前登录”，确认身份与显示名称。目录使用内部 UUID，完整邮箱可以用作显示名称。
4. 添加其他账号时，先保留当前快照，再自行通过官方客户端登录其他账号；退出后回来收录。
5. 有快照的目标账号显示“切换账号”。切换前再次退出 Claude；工具保留当前账号的最新状态，再恢复目标快照。
6. 自行重新打开 Claude 验证登录。官方登出、令牌撤销、过期或客户端更新都可能使旧快照失效，需要重新登录。

完整步骤、第三方配置、备份恢复和常见问题见 [使用手册](docs/USER_GUIDE.md)。

## 数据与隐私

程序自己的偏好、请求记录和备份保存在安装目录的 `data/`；桌面版登录快照保存在当前 Windows 用户的 `.claude-profiles/`。认证备份包含敏感文件，限制访问权限并与普通会话备份分开。**不要上传运行数据或认证备份。** 卸载不会删除运行后产生的 `data/` 或 Claude 自己的数据；公开源码及安装包不包含任何用户账号、对话或快照。

需要网络的操作包括用户选择的提供商推理以及通过官方客户端登录。基础本地浏览和文件管理不需要工具自己的云端服务。对于有模型映射的第三方提供商，工具会使用只监听回环地址的本地转发器；其凭证与官方订阅登录分离。

## 开发与构建

源码入口：

| 部分 | 路径 | 职责 |
| --- | --- | --- |
| Rust 核心 | `rust-core/` | 扫描、去重、用量、备份、受保护的操作分发 |
| 本地适配器 | `local_adapter.py` | 快照、事务记录、第三方配置与可选本地转发 |
| WPF 界面 | `Studio.ps1` / `Studio.xaml` | 账号卡片、共享池、引导与设置 |
| EXE 入口 | `launcher/` | 隐藏控制台启动界面、实例锁和文件自检 |
| 安装器 | `installer/` | 中文/英文安装、快捷方式、保留数据的卸载 |
| 图标源文件 | `assets/app.svg` | 原创矢量图标，附 PNG 和多尺寸 ICO |

构建依赖：Windows 自带 PowerShell 5.1、.NET Framework C# 编译器，Rust GNU 工具链，以及 Inno Setup 6.7.3。运行版 Python 3.14.10 从 Python 官网下载，构建脚本核对固定 SHA-256，不使用开发者个人 Python 打包。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File packaging\build-release.ps1
```

构建配置与验收方式见 [构建和架构说明](docs/DEVELOPMENT.md)。

## 许可与致谢

本项目新增代码采用 [MIT](LICENSE)。历史原型的许可保存在 [UPSTREAM-LICENSE](UPSTREAM-LICENSE)。已复用的来源、纯设计参考、Rust 依赖、Python 与打包工具分别列在 [第三方声明](THIRD_PARTY_NOTICES.md) 和 [开源审计说明](docs/OPEN_SOURCE_AUDIT.md)。第三方组件保持各自许可，不因本项目使用 MIT 而改变。

不附带 Claude、CC Switch 或其他参考项目的商标和官方图标；品牌名称仅用于描述兼容对象，不表示得到官方认可或合作授权。
