# 架构、构建与验收

## 架构

EXE 入口是一个小型 .NET Framework Windows 程序，使用图标和实例锁，并在无控制台窗口的情况下启动系统 PowerShell 5.1/WPF。它不启动 Claude。

WPF 以 JSON 请求调用 Rust 核心。读取路径包含账号、邮箱来源、对话去重、原文状态和历史用量。认证变更必须携带明确的维护确认；核心再次检查进程，并分发给本地 Python 适配器。

Python 适配器处理带时间戳的受保护快照、账号身份核对、复制校验、公共操作锁与持久事务记录；独立处理第三方配置和可选的回环转发。Rust 优先调用程序目录的 `runtime/python/python.exe`，开发环境才回退到用户安装的 Python。账号、会话、记忆、认证和服务端缓存是不同层次，不能把它们视为可互换的数据。

当前 `data/` 位于程序目录。登录快照位于用户的 `.claude-profiles/`。任何更新不得自动迁移、清空、重命名或上传这些目录。

## 构建环境

需要 Windows 10/11 x64、系统 PowerShell 5.1、.NET Framework C# 编译器、Rust GNU 工具链和 Inno Setup 6.7.3。图标的 SVG、PNG 和 ICO 已纳入源码，普通构建无需安装绘图库。重新生成 ICO 才需要开发环境的 Pillow。

本项目的本地构建脚本默认使用隔离工具目录：

```text
D:\programfile\RustStudioToolchain\cargo\bin\cargo.exe
D:\programfile\RustStudioToolchain\rustup\toolchains\stable-x86_64-pc-windows-gnu\
D:\programfile\ClaudeSwitchBuildTools\InnoSetup6\ISCC.exe
```

可以通过 `-ToolRoot` 和 `-Compiler` 改为自己的布局；`ToolRoot` 下使用 `cargo`、`rustup` 两个子目录。安装 Rust 时设置对应的 `CARGO_HOME`、`RUSTUP_HOME`，再安装 `stable-x86_64-pc-windows-gnu` 工具链。GNU 输出目录使用纯 ASCII 路径，以避开部分链接工具对中文路径的限制。普通标准 Cargo 安装用户也可以手工执行 `cargo test/build --locked --manifest-path rust-core/Cargo.toml` 并将发行 EXE 放入源码根目录，再使用打包脚本的 `-SkipCompile`。

```powershell
# 下载固定 Python 运行时并核对 SHA-256，编译入口和核心，收集许可，生成安装包。
powershell.exe -NoProfile -ExecutionPolicy Bypass -File packaging\build-release.ps1
```

Inno 编译器从 [官网](https://jrsoftware.org/isdl.php) 安装。中文语言文件从官方源仓库获取，保留原作者声明。首次生成 `packaging/payload` 后再次构建会停止，以免递归清理混入的用户数据；请在新的源码工作副本中构建，或自行归档上一份 payload 后重试。

Python 固定为 3.14.10 官方 Windows x64 embedded 包，校验值来自 [官方发布页](https://www.python.org/downloads/release/python-31410/)：

```text
90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159
```

源码不提交下载缓存、已解压运行时、安装包或个人数据；运行时只随 release 安装包发布。`collect-licenses.ps1` 从精确 Cargo Windows 目标元数据、本地 crate 原文和官方运行时源收集许可。

## 测试

```powershell
python -m unittest discover -s tests -v
cargo test --locked --manifest-path rust-core/Cargo.toml
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\test-ui-preferences.ps1
```

Python 用例只使用临时样例账号、模拟提供商与本机测试端口，覆盖错误身份拒绝、快照校验、令牌轮换保存、跨盘归档、常规失败和进程中断。Windows Junction 用例只链接临时样例目录。

WPF 的 `-SmokeTest -ReadOnly -SnapshotDir <测试输出目录>` 只读取本地已有目录，渲染工具自己的界面，验证矢量对齐、引导、邮箱和只读守卫；不执行真实账号操作，也不应将个人界面截图上传仓库。

安装器用 `installer/verify-installer.ps1` 验证首次安装、逐文件 hash、重装保留样例数据、卸载保留样例数据。**必须在正式安装之前运行**：临时安装目录仍使用相同 AppId，脚本发现既有正式卸载注册时会拒绝执行。

真实认证验收必须得到用户确认，先提醒完全退出 Claude，按时间备份，再执行；不得把文件校验或虚拟测试称为在线登录验证。

## 公开发布检查

- 源码使用明确白名单，禁止上传 `data/`、`.claude*`、认证缓存、索引、原文、私密备份、验收日志和含邮箱的截图。
- 公开测试使用合成 UUID 和 `example.test` 邮箱；不提交真实用户的 Windows 路径或账号对应关系。
- 安装 payload 检查链接、敏感目录和认证文件，固定依赖版本，并附许可证、版权、完整运行时声明。
- 自有源码 MIT 不覆盖第三方许可或服务条款；参考项目的名称与标志不得暗示授权或合作。
- Release 上传安装包与 SHA-256；未经代码签名的安装版应明确说明。
