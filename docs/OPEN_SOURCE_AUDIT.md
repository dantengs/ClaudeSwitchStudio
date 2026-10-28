# 开源来源与分发许可核对

核对日期：2026-10-28（北京时间）。适用版本：Claude Switch Studio 0.3.1，Windows x64 安装版。

本文件区分历史原型复用、运行时依赖和仅供参考的项目。项目本身的许可证不能替代第三方组件的许可证；发布时须同时保留实际分发组件的声明。

## 参考项目与实际关系

| 项目 | 本项目与其关系 | 上游已核对许可 | 发布处理 |
| --- | --- | --- | --- |
| [Sevin47/claude-desktop-switcher](https://github.com/Sevin47/claude-desktop-switcher) | 早期 PowerShell 原型来源。现版已改为 Rust 数据核心、Python 本地适配器和 WPF 界面；仍保守保留历史来源声明，不宣称全部历史代码均为原创 | [MIT](https://github.com/Sevin47/claude-desktop-switcher/blob/main/LICENSE)，Copyright (c) 2026 Sevin Eldridge | 将现有 `UPSTREAM-LICENSE` 原文随源代码与安装版分发，保留作者及完整许可条件 |
| [farion1231/cc-switch](https://github.com/farion1231/cc-switch) | 参考账号卡片与设置的信息组织、官方账号管理思路；可只读识别用户自行安装的 CC Switch 的本地 SQLite 提供商配置。没有把该应用或其登录客户端捆绑进安装包 | [MIT](https://github.com/farion1231/cc-switch/blob/main/LICENSE)，Copyright (c) 2025 Jason Young | 在项目介绍中注明界面与设计参考及互操作关系；不要将 CC Switch 说成本项目的运行时组件，也不要声称本项目获得其或 Anthropic 的官方背书 |
| [Zoltak-Dev/ai-multi-instance](https://github.com/Zoltak-Dev/ai-multi-instance) | 仅调研 Windows Desktop 多实例、登录回调和用户数据目录机制；没有复制或执行其强制结束进程、删除目录等实现 | [MIT](https://github.com/Zoltak-Dev/ai-multi-instance/blob/main/LICENSE)，许可证署名为 Copyright (c) 2026 **Zoltrak-Dev**，与仓库账号拼写不同，应按原文保留 | 作为技术调研来源列出；不要写成代码分发或底层内核来源 |
| [mysqto/ccswitch](https://github.com/mysqto/ccswitch) | 仅调研凭证与身份元数据配套、切换前保存最新令牌状态、独立目录和共享本地数据的设计；没有复制其 Rust 源码 | [MIT](https://github.com/mysqto/ccswitch/blob/main/LICENSE)，Copyright (c) 2026 Chen Lei | 作为设计参考列出；不能仅因同为 Rust 实现而描述为其派生版本 |
| 用户提供的 CC Switch / Antigravity Tools 截图 | 视觉布局和阅读体验参考，未将截图、图标、品牌标志或其他图像素材作为安装版资源 | 截图和品牌素材的许可证未作推定 | 使用本项目自己制作的图标；公开页面不重新分发这些截图或上游品牌素材 |

以上“未复制代码”是本轮开发范围与来源记录的描述，并非逐行代码相似性检测结论。以后若复制其他项目的代码或资源，应按实际内容重新核对、补充作者与许可，不能继续沿用“仅参考”的描述。

## Rust 核心与 Cargo 依赖

核对依据为项目 `Cargo.lock`、Cargo 的 Windows GNU 目标解析结果，以及已缓存 crate 包内的实际许可文件。命令为：

```powershell
cargo metadata --offline --locked --filter-platform x86_64-pc-windows-gnu --manifest-path rust-core/Cargo.toml --format-version 1
```

必须保留目标过滤：无过滤时 Cargo 会尝试解析其他目标的未缓存包，不能将离线失败误认为已完整核对。以下包含目标解析涉及的构建依赖；以完整清单保守披露，并不表示每个包都有运行时代码进入最终 EXE。

| crate | 锁定版本 | Cargo 许可表达式 | 缓存内实际许可文件 |
| --- | --- | --- | --- |
| adler2 | 2.0.1 | 0BSD OR MIT OR Apache-2.0 | LICENSE-0BSD、LICENSE-MIT、LICENSE-APACHE |
| autocfg | 1.5.1 | Apache-2.0 OR MIT | LICENSE-MIT、LICENSE-APACHE |
| block-buffer | 0.10.4 | MIT OR Apache-2.0 | LICENSE-MIT、LICENSE-APACHE |
| byteorder | 1.5.0 | Unlicense OR MIT | COPYING、LICENSE-MIT、UNLICENSE |
| cfg-if | 1.0.5 | MIT OR Apache-2.0 | LICENSE-MIT、LICENSE-APACHE |
| chrono | 0.4.45 | MIT OR Apache-2.0 | LICENSE.txt，含双重许可说明及全文 |
| cpufeatures | 0.2.17 | MIT OR Apache-2.0 | LICENSE-MIT、LICENSE-APACHE |
| crc32fast | 1.5.3 | MIT OR Apache-2.0 | LICENSE-MIT、LICENSE-APACHE |
| crypto-common | 0.1.7 | MIT OR Apache-2.0 | LICENSE-MIT、LICENSE-APACHE |
| digest | 0.10.7 | MIT OR Apache-2.0 | LICENSE-MIT、LICENSE-APACHE |
| flate2 | 1.1.10 | MIT OR Apache-2.0 | LICENSE-MIT、LICENSE-APACHE |
| fs2 | 0.4.3 | MIT/Apache-2.0 | LICENSE-MIT、LICENSE-APACHE |
| generic-array | 0.14.7 | MIT | LICENSE |
| itoa | 1.0.18 | MIT OR Apache-2.0 | LICENSE-MIT、LICENSE-APACHE |
| memchr | 2.8.3 | Unlicense OR MIT | COPYING、LICENSE-MIT、UNLICENSE |
| miniz_oxide | 0.9.1 | MIT OR Zlib OR Apache-2.0 | LICENSE、LICENSE-MIT.md、LICENSE-ZLIB.md、LICENSE-APACHE.md |
| num-traits | 0.2.19 | MIT OR Apache-2.0 | LICENSE-MIT、LICENSE-APACHE |
| serde_core | 1.0.229 | MIT OR Apache-2.0 | LICENSE-MIT、LICENSE-APACHE |
| serde_json | 1.0.154 | MIT OR Apache-2.0 | LICENSE-MIT、LICENSE-APACHE |
| sha2 | 0.10.9 | MIT OR Apache-2.0 | LICENSE-MIT、LICENSE-APACHE |
| simd-adler32 | 0.3.10 | MIT | LICENSE.md |
| typenum | 1.20.1 | MIT OR Apache-2.0 | LICENSE、LICENSE-MIT、LICENSE-APACHE |
| version_check | 0.9.5 | MIT/Apache-2.0 | LICENSE-MIT、LICENSE-APACHE |
| winapi | 0.3.9 | MIT/Apache-2.0 | LICENSE-MIT、LICENSE-APACHE |
| winapi-x86_64-pc-windows-gnu | 0.4.0 | MIT/Apache-2.0 | 此 crate 压缩包未带单独许可文件，见下文补充 |
| zip | 0.6.6 | MIT | LICENSE |
| zmij | 1.0.23 | MIT | LICENSE-MIT |

注意事项：

- `OR` 表示可以选择一种许可，并非同时叠加全部条件。旧 Cargo 表达式中的 `/` 是历史写法；这些包的上游双许可文件已一并存在。为避免丢失作者说明，发布包可保留实际包中的全部许可、COPYING、NOTICE 与 UNLICENSE 文件，不应只输出一个公共 MIT 模板。
- 上表所有 crate 都提供 MIT 选项或直接使用 MIT；这不能替代保留它们各自的版权声明。若以 Apache-2.0 选项分发，也须遵守其适用的许可与 NOTICE 要求。
- `winapi-x86_64-pc-windows-gnu` 的 Cargo 包只包含源码、导入库及清单，未把许可证文件打进 crate。其清单声明仓库为 [retep998/winapi-rs](https://github.com/retep998/winapi-rs)，许可表达式为 `MIT/Apache-2.0`。发布清单应记录此事实，并为该组件补附同一上游项目的 [LICENSE-MIT](https://github.com/retep998/winapi-rs/blob/0.3.9/LICENSE-MIT) 和 LICENSE-APACHE；可以从已缓存的 `winapi-0.3.9` 复制这两份上游原文，注明来源。不要因自动扫描不到本地 LICENSE 而省略该组件。
- Rust 标准库会被链接到程序中，GNU 目标的编译器运行时支持也有独立声明。构建所用工具链的 `share/doc/rust/COPYRIGHT-library.html` 是标准库及其构建依赖的版权与许可汇总；本轮发布流程还保守附带同目录 `COPYRIGHT.html` 及完整 `licenses/`，其中含 GCC 和 LLVM 例外文本，供用户核查。编译器自身仅用于构建，不作为安装载荷；附带更宽范围的声明不表示所有被列出的源码都进入最终 EXE。不要只依赖 Cargo 包清单，也不要将这些特定组件的 GPL/GCC 或 LLVM 例外笼统改写为本项目采用 GPL。

## Python 嵌入式运行时

安装版计划携带官方 Python 3.14.10 Windows x64 embeddable 发行包。应完整保留该包自带的 `LICENSE.txt`，包括其收录的第三方组件声明，而不是仅列出“PSF License”。官方 Python 包内还可能有其他声明文件，构建白名单不能无意间删去。

Python 的 PSF License 允许复制和分发，但要求保留许可与版权；若修改 Python 本身，应按许可补充变更说明。本项目使用未经修改的官方运行时；应用自己的 `local_adapter.py` 和运行时路径配置，应与“修改 CPython 本体”分开描述。依据：[Python History and License](https://docs.python.org/3.14/license.html)。最终打包时还需实际核对 3.14.10 下载包的文件清单，而非用开发机另一个 Python 版本的许可文件代替。

本轮打包对实际运行时额外核对了 OpenSSL：仅保留嵌入式包 `LICENSE.txt` 不足以明确列出该二进制的所有组件。因此发布流程还应保留 Python 官方许可与致谢页面副本，并按实际 `_ssl` 返回的 OpenSSL 3.5.9 版本，补附上游对应标签中的 `LICENSE.txt`、`ACKNOWLEDGEMENTS.md` 和 `AUTHORS.md`。该版本上游根目录没有 `NOTICE` 文件，不应伪造一份并称其来自上游；其他组件若实际附带 NOTICE，仍须保留其原文。不要把开发机 OpenSSL 或其他 Python 版本的声明混入这一清单。最终文件与版本核对结果由构建记录保存。

## Inno Setup 安装器

使用 Inno Setup 6.7.3 编译安装 EXE，不把 ISCC 编译器和整个开发环境作为产品载荷。许可证来源为该版本官方仓库的 [Inno Setup License](https://github.com/jrsoftware/issrc/blob/is-6_7_3/license.txt)，并核对了 [官网说明](https://jrsoftware.org/isinfo.php)。

Inno Setup 使用自己的宽松许可，不是 MIT，也不能假设它需要 GPL“安装器运行时例外”。已核对条文允许包括商业用途在内的使用、修改与分发，要求保留源码中的版权和条件、保留二进制已有的版权与网址、不冒称原作者、对修改版作出区分。用它分发产品时在产品文档致谢被表述为欢迎但非必需；本项目仍会明确致谢并随附完整许可。官网同时请求商业用户购买商业许可，应按实际使用性质阅读其购买说明，不能把一句“开源免费”当作全部使用说明，也不能凭空增加安装包强制按份付费的说法。

最终发布时应保留安装器本身的上游版权与网址，不移除或伪造 Inno 的声明；更换产品安装图标并不代表修改其作者归属。

## Windows、.NET 与 WPF

启动器及界面使用 Windows 已提供的 .NET Framework / WPF / Windows PowerShell。它们是系统组件，不是本项目原创代码，也不应把开发机 `C:\Windows` 下的系统 DLL 直接复制到产品中。若未来捆绑其他 .NET 发行运行时，应单独按该发行包的许可与再分发条款补充审计。

## 发布前清单

1. 为本项目自己的代码提供明确的顶层 LICENSE，与 `rust-core/Cargo.toml` 的 MIT 声明保持一致；不能用 `UPSTREAM-LICENSE` 代替项目自身的许可选择。
2. 保留 Sevin 原型的 `UPSTREAM-LICENSE` 原文；在介绍文档明确原型、仅设计参考与实际运行时依赖的不同关系。
3. 安装版随附 `third-party-licenses/`：27 个目标解析 crate 的原文、winapi 导入库的上游许可补充、Rust 标准库汇总与所引用的许可文本、Inno Setup 6.7.3 完整许可。
4. 保留嵌入式 Python 官方包自带的完整 LICENSE 与必要声明。
5. 不打包 CC Switch、ai-multi-instance、mysqto/ccswitch 的程序，不打包用户提供的界面截图或上游品牌图标；若日后改变这一点，先更新本表和分发声明。
6. 许可附件的生成只针对构建依赖和官方发行包，不读取或携带账号凭证、登录快照、个人邮箱映射、对话数据或私密备份。

本文件记录核对方法和发布所需材料，不代替第三方许可证原文。构建文件或依赖锁定版本改变后应重新生成组件清单。
