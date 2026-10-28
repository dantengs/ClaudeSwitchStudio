# Third-party notices

Original project code: MIT; third-party material keeps its own license. See LICENSE and docs/OPEN_SOURCE_AUDIT.md. The historical Sevin47 prototype notice is retained in UPSTREAM-LICENSE. CC Switch and the other researched applications are not bundled.

## Rust application dependencies

For each package below the project elects the MIT option where the upstream offers alternatives. Original license files (including alternative texts and notices where present) are preserved, unmodified, in third-party-licenses. Build dependencies are included for completeness.

| Package | Version | Upstream license expression | Notices directory |
| --- | --- | --- | --- |
| adler2 | 2.0.1 | 0BSD OR MIT OR Apache-2.0 | `rust-crates/adler2-2.0.1` |
| autocfg | 1.5.1 | Apache-2.0 OR MIT | `rust-crates/autocfg-1.5.1` |
| block-buffer | 0.10.4 | MIT OR Apache-2.0 | `rust-crates/block-buffer-0.10.4` |
| byteorder | 1.5.0 | Unlicense OR MIT | `rust-crates/byteorder-1.5.0` |
| cfg-if | 1.0.5 | MIT OR Apache-2.0 | `rust-crates/cfg-if-1.0.5` |
| chrono | 0.4.45 | MIT OR Apache-2.0 | `rust-crates/chrono-0.4.45` |
| cpufeatures | 0.2.17 | MIT OR Apache-2.0 | `rust-crates/cpufeatures-0.2.17` |
| crc32fast | 1.5.3 | MIT OR Apache-2.0 | `rust-crates/crc32fast-1.5.3` |
| crypto-common | 0.1.7 | MIT OR Apache-2.0 | `rust-crates/crypto-common-0.1.7` |
| digest | 0.10.7 | MIT OR Apache-2.0 | `rust-crates/digest-0.10.7` |
| flate2 | 1.1.10 | MIT OR Apache-2.0 | `rust-crates/flate2-1.1.10` |
| fs2 | 0.4.3 | MIT/Apache-2.0 | `rust-crates/fs2-0.4.3` |
| generic-array | 0.14.7 | MIT | `rust-crates/generic-array-0.14.7` |
| itoa | 1.0.18 | MIT OR Apache-2.0 | `rust-crates/itoa-1.0.18` |
| memchr | 2.8.3 | Unlicense OR MIT | `rust-crates/memchr-2.8.3` |
| miniz_oxide | 0.9.1 | MIT OR Zlib OR Apache-2.0 | `rust-crates/miniz_oxide-0.9.1` |
| num-traits | 0.2.19 | MIT OR Apache-2.0 | `rust-crates/num-traits-0.2.19` |
| serde_core | 1.0.229 | MIT OR Apache-2.0 | `rust-crates/serde_core-1.0.229` |
| serde_json | 1.0.154 | MIT OR Apache-2.0 | `rust-crates/serde_json-1.0.154` |
| sha2 | 0.10.9 | MIT OR Apache-2.0 | `rust-crates/sha2-0.10.9` |
| simd-adler32 | 0.3.10 | MIT | `rust-crates/simd-adler32-0.3.10` |
| typenum | 1.20.1 | MIT OR Apache-2.0 | `rust-crates/typenum-1.20.1` |
| version_check | 0.9.5 | MIT/Apache-2.0 | `rust-crates/version_check-0.9.5` |
| winapi | 0.3.9 | MIT/Apache-2.0 | `rust-crates/winapi-0.3.9` |
| winapi-x86_64-pc-windows-gnu | 0.4.0 | MIT/Apache-2.0 | `rust-crates/winapi-x86_64-pc-windows-gnu-0.4.0` |
| zip | 0.6.6 | MIT | `rust-crates/zip-0.6.6` |
| zmij | 1.0.23 | MIT | `rust-crates/zmij-1.0.23` |

## Runtime and packaging

- Rust standard library and toolchain runtime notices: `third-party-licenses/rust-toolchain/`, with the upstream COPYRIGHT pages and their referenced license texts, including the GCC Runtime Library Exception where applicable. These notices do not relicense this application as GPL.
- Python 3.14.10 Windows x64 embedded distribution: `runtime/python/LICENSE.txt`, `third-party-licenses/python/LICENSE.txt`, and the upstream Licenses and Acknowledgements document. Python and its Windows distributable code retain their original conditions; do not remove notices or redistribute the Windows runtime on unsupported platforms.
- The Python distribution includes OpenSSL 3.5.9. Its unmodified Apache-2.0 license, AUTHORS and ACKNOWLEDGEMENTS are separately retained under `third-party-licenses/python/`; that upstream tag has no NOTICE file. Other Python component notices are retained in the official distribution license and acknowledgements document.
- Inno Setup 6.7.3: installer built with the original compiler; no compiler is bundled. Its license is retained under `third-party-licenses/inno-setup/`. Original installer copyright and website identification are not removed.
- Windows PowerShell, WPF and .NET Framework are used from Windows and are not copied into the payload. The original SVG/PNG/ICO application artwork is part of this project under its MIT license.

Sources: https://www.python.org/downloads/release/python-31410/ ; https://docs.python.org/3.14/license.html ; https://github.com/openssl/openssl/tree/openssl-3.5.9 ; https://github.com/rust-lang/rust ; https://github.com/jrsoftware/issrc/tree/is-6_7_3
