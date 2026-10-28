mod workspace;
// Rust local workspace core. Live mutations require an explicitly confirmed maintenance window.
use chrono::{DateTime, FixedOffset, TimeZone, Utc};
use fs2::FileExt;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::time::{SystemTime, UNIX_EPOCH};
use std::{
    env,
    fs::{self, File, OpenOptions},
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::Command,
};
use zip::{write::FileOptions, ZipArchive, ZipWriter};
type Result<T> = std::result::Result<T, String>;
fn now_nanos() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
}
fn local_now() -> DateTime<FixedOffset> {
    let ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64;
    Utc.timestamp_millis_opt(ms)
        .unwrap()
        .with_timezone(&FixedOffset::east_opt(8 * 3600).unwrap())
}
fn err(e: impl std::fmt::Display) -> String {
    e.to_string()
}
fn read_json(p: &Path) -> Result<Value> {
    let s = fs::read_to_string(p).map_err(err)?;
    serde_json::from_str(s.trim_start_matches('\u{feff}')).map_err(err)
}
fn opt_json(p: &Path) -> Value {
    read_json(p).unwrap_or(Value::Null)
}
fn s<'a>(v: &'a Value, k: &str) -> &'a str {
    v[k].as_str().unwrap_or("")
}
fn short(v: &str) -> String {
    v.chars().take(8).collect()
}
fn write_json(p: &Path, v: &Value) -> Result<()> {
    if let Some(parent) = p.parent() {
        fs::create_dir_all(parent).map_err(err)?;
    }
    let temporary = p.with_file_name(format!(
        ".{}.{}-{}.tmp",
        name(p),
        std::process::id(),
        now_nanos()
    ));
    let mut f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(err)?;
    f.write_all(serde_json::to_string_pretty(v).map_err(err)?.as_bytes())
        .map_err(err)?;
    f.sync_all().map_err(err)?;
    drop(f);
    fs::rename(temporary, p).map_err(err)
}
fn hash(p: &Path) -> Result<String> {
    let mut f = File::open(p).map_err(err)?;
    hash_reader(&mut f)
}
fn hash_reader(r: &mut impl Read) -> Result<String> {
    let mut h = Sha256::new();
    let mut b = [0; 65536];
    loop {
        let n = r.read(&mut b).map_err(err)?;
        if n == 0 {
            break;
        }
        h.update(&b[..n]);
    }
    Ok(format!("{:x}", h.finalize()))
}
fn uuid(v: &str) -> Result<()> {
    if v.len() == 36
        && v.chars().enumerate().all(|(i, c)| {
            if [8, 13, 18, 23].contains(&i) {
                c == '-'
            } else {
                c.is_ascii_hexdigit()
            }
        })
    {
        Ok(())
    } else {
        Err("账号或组织标识无效。".into())
    }
}
fn files(root: &Path) -> Result<Vec<PathBuf>> {
    if !root.exists() {
        return Ok(vec![]);
    }
    let mut out = vec![];
    let mut pending = vec![root.to_owned()];
    while let Some(dir) = pending.pop() {
        for entry in fs::read_dir(&dir).map_err(err)? {
            let e = entry.map_err(err)?;
            let kind = e.file_type().map_err(err)?;
            if kind.is_symlink() {
                return Err("数据目录中存在链接，已停止。".into());
            }
            if kind.is_dir() {
                pending.push(e.path())
            } else if kind.is_file() {
                out.push(e.path())
            }
        }
    }
    out.sort();
    Ok(out)
}
fn dirs(root: &Path) -> Result<Vec<PathBuf>> {
    if !root.exists() {
        return Ok(vec![]);
    }
    let mut out = vec![];
    for e in fs::read_dir(root).map_err(err)? {
        let e = e.map_err(err)?;
        if e.file_type().map_err(err)?.is_dir() {
            out.push(e.path())
        }
    }
    out.sort();
    Ok(out)
}
fn name(p: &Path) -> String {
    p.file_name()
        .unwrap_or_default()
        .to_string_lossy()
        .into_owned()
}
fn command_hidden(program: &str) -> Command {
    let mut c = Command::new(program);
    #[cfg(windows)]
    c.creation_flags(0x08000000);
    c
}
#[cfg(windows)]
fn watcher_running() -> bool {
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn OpenMutexW(access: u32, inherit: i32, name: *const u16) -> *mut std::ffi::c_void;
        fn CloseHandle(handle: *mut std::ffi::c_void) -> i32;
    }
    let name: Vec<u16> = "Global\\ClaudeAutoSwitchWatcher"
        .encode_utf16()
        .chain(Some(0))
        .collect();
    // SAFETY: the name is NUL-terminated and the returned owned handle is closed once.
    unsafe {
        let handle = OpenMutexW(0x00100000, 0, name.as_ptr());
        if handle.is_null() {
            false
        } else {
            CloseHandle(handle);
            true
        }
    }
}
#[cfg(not(windows))]
fn watcher_running() -> bool {
    false
}
struct Context {
    app: PathBuf,
    claude: PathBuf,
    profiles: PathBuf,
    data: PathBuf,
    kernel: PathBuf,
    fixture: bool,
}
impl Context {
    fn new(base: PathBuf, kernel: PathBuf, fixture: Option<PathBuf>) -> Result<Self> {
        if let Some(r) = fixture {
            return Ok(Self {
                app: r.join("desktop"),
                claude: r.join("claude"),
                profiles: r.join("profiles"),
                data: r.join("data"),
                kernel,
                fixture: true,
            });
        }
        let user = PathBuf::from(env::var("USERPROFILE").map_err(err)?);
        let local = PathBuf::from(env::var("LOCALAPPDATA").unwrap_or_default());
        let roaming = PathBuf::from(env::var("APPDATA").unwrap_or_default());
        let candidates = [
            local.join("Claude-Data/claude_pzs8sxrjxfjjc"),
            user.join("AppData/Local/Claude-Data/claude_pzs8sxrjxfjjc"),
            roaming.join("Claude"),
            local.join("Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude"),
        ];
        let app = candidates
            .into_iter()
            .find(|p| p.join("config.json").is_file())
            .ok_or("未找到 Claude 桌面端数据目录。")?;
        Ok(Self {
            app,
            claude: user.join(".claude"),
            profiles: user.join(".claude-profiles"),
            data: base.join("data"),
            kernel,
            fixture: false,
        })
    }
    fn registry(&self) -> PathBuf {
        self.app.join("claude-code-sessions")
    }
    fn transcripts(&self) -> Result<std::collections::BTreeMap<String, PathBuf>> {
        let mut map = std::collections::BTreeMap::new();
        for p in files(&self.claude.join("projects"))? {
            if p.extension().and_then(|x| x.to_str()) == Some("jsonl") {
                let k = p.file_stem().unwrap().to_string_lossy().to_string();
                if map.insert(k, p).is_some() {
                    return Err("发现重复原文 ID，请先检查数据目录。".into());
                }
            }
        }
        Ok(map)
    }
    fn profiles(&self) -> Result<std::collections::BTreeMap<String, Value>> {
        let mut map = std::collections::BTreeMap::new();
        for p in dirs(&self.profiles)? {
            let n = name(&p);
            if n.starts_with('.') {
                continue;
            }
            let cfg = opt_json(&p.join("config.json"));
            let id = s(&cfg, "lastKnownAccountUuid");
            if uuid(id).is_err() {
                continue;
            }
            let metadata = opt_json(&p.join("studio-profile.json"));
            let complete = (s(&metadata, "Account").is_empty() || s(&metadata, "Account") == id)
                && [
                    "Local State",
                    "Network",
                    "Local Storage",
                    "Session Storage",
                    "config.json",
                    "claude.json",
                ]
                .iter()
                .filter(|x| **x != "claude.json" || !p.join("studio-profile.json").exists())
                .all(|x| p.join(x).exists())
                && ["oauth:tokenCache", "oauth:tokenCacheV2"]
                    .iter()
                    .any(|key| {
                        cfg[*key].as_str().is_some_and(|value| !value.is_empty())
                            || cfg[*key].as_object().is_some_and(|value| !value.is_empty())
                    });
            let label = fs::read_to_string(p.join("label.txt"))
                .unwrap_or_else(|_| {
                    let display = s(&metadata, "DisplayName");
                    if !display.is_empty() {
                        display.to_owned()
                    } else if !n.starts_with("desktop-") {
                        n.clone()
                    } else {
                        String::new()
                    }
                })
                .trim_matches('\u{feff}')
                .trim()
                .to_owned();
            map.insert(
                id.into(),
                json!({"Name":n,"Label":label,"Complete":complete}),
            );
        }
        Ok(map)
    }
    fn state_base(&self) -> Result<Value> {
        let cfg = opt_json(&self.app.join("config.json"));
        let live = s(&cfg, "lastKnownAccountUuid");
        let labels = opt_json(&self.data.join("labels.json"));
        let profiles = self.profiles()?;
        let tx = self.transcripts()?;
        let mut ids: std::collections::BTreeSet<String> = profiles.keys().cloned().collect();
        for p in dirs(&self.registry())? {
            ids.insert(name(&p));
        }
        if !live.is_empty() {
            ids.insert(live.into());
        }
        let (mut accounts, mut sessions, mut targets) = (vec![], vec![], vec![]);
        for id in ids {
            uuid(&id)?;
            let profile = profiles.get(&id).cloned().unwrap_or(Value::Null);
            let mut label = s(&labels, &id).to_owned();
            if label.is_empty() {
                label = s(&profile, "Label").to_owned();
            }
            let custom_name = !label.is_empty();
            if label.is_empty() {
                label = format!("账号 {}", short(&id));
            }
            let (mut count, mut orgs) = (0, 0);
            for org in dirs(&self.registry().join(&id))? {
                let org_id = name(&org);
                uuid(&org_id)?;
                orgs += 1;
                let mut oc = 0;
                for p in files(&org)? {
                    let fname = name(&p);
                    if !fname.starts_with("local_") || !fname.ends_with(".json") {
                        continue;
                    }
                    let j = read_json(&p)?;
                    let cli = s(&j, "cliSessionId");
                    let last = j["lastActivityAt"].as_i64().unwrap_or(0);
                    let when = FixedOffset::east_opt(8 * 3600)
                        .unwrap()
                        .timestamp_millis_opt(last)
                        .single()
                        .map(|d| d.format("%m-%d %H:%M").to_string())
                        .unwrap_or_else(|| "未知".into());
                    let path = tx.get(cli);
                    let size = path
                        .and_then(|p| fs::metadata(p).ok())
                        .map(|m| format!("{:.1} MB", m.len() as f64 / 1048576.0))
                        .unwrap_or_else(|| "—".into());
                    sessions.push(json!({"Account":id,"AccountName":label,"Org":org_id,"Id":j["sessionId"],"CliId":cli,"Title":j["title"],"Cwd":j["cwd"],"When":when,"LastAt":last,"Model":j["model"],"Archived":j["isArchived"].as_bool().unwrap_or(false),"HasTranscript":path.is_some(),"Health":if path.is_some(){"原文完整"}else{"缺少原文"},"Size":size,"Path":p,"Transcript":path.cloned().unwrap_or_default()}));
                    count += 1;
                    oc += 1;
                }
                targets.push(json!({"Account":id,"Org":org_id,"Name":format!("{} / {} · {} 条",label,short(&org_id),oc)}));
            }
            let current = id == live;
            let saved = profile["Complete"].as_bool().unwrap_or(false);
            accounts.push(json!({"Uuid":id,"Short":short(&id),"Name":label,"HasCustomName":custom_name,"Initial":label.chars().next().map(|c|c.to_string()).unwrap_or_default(),"Sessions":count,"Organizations":orgs,"IsCurrent":current,"Profile":s(&profile,"Name"),"Saved":saved,"CanSwitch":saved&&!current,"Status":if current{"当前账号"}else if saved{"可切换"}else{"待保存登录"},"ProfileStatus":if saved{"已保存登录状态"}else{"发现本地会话 · 尚未保存登录"},"CurrentVisibility":if current{"Visible"}else{"Collapsed"}}));
        }
        sessions.sort_by_key(|x| std::cmp::Reverse(x["LastAt"].as_i64().unwrap_or(0)));
        let mut backups = vec![];
        for dir in dirs(&self.data.join("backups"))?.into_iter().rev() {
            let m = opt_json(&dir.join("manifest.json"));
            if m.is_null() {
                continue;
            }
            let size = fs::metadata(dir.join("conversation-data.zip"))
                .map(|x| format!("{:.1} MB", x.len() as f64 / 1048576.0))
                .unwrap_or_else(|_| "缺失".into());
            let undone = m["Undone"].as_bool().unwrap_or(false);
            backups.push(json!({"Id":name(&dir),"Time":m["Time"],"Files":m["Files"].as_array().map(|x|x.len()).unwrap_or(0),"Size":size,"Reason":m["Reason"],"Path":dir,"CanUndo":m["Created"].as_array().is_some_and(|a|!a.is_empty())&&!undone,"Status":if undone{"已撤回新增"}else{"校验通过"}}));
        }
        Ok(
            json!({"Backend":"Rust 0.1","Live":live,"Accounts":accounts,"Sessions":sessions,"Targets":targets,"Backups":backups,"TranscriptCount":tx.len(),"Watcher":watcher_running(),"AppRoot":self.app,"Kernel":self.kernel}),
        )
    }
    fn state(&self) -> Result<Value> {
        workspace::enrich(self, self.state_base()?)
    }
    fn usage(&self, r: &Value) -> Result<Value> {
        if s(r, "PoolKey").is_empty() {
            uuid(s(r, "Account"))?;
        }
        let st = self.state()?;
        let rows = if s(r, "PoolKey").is_empty() {
            st["Sessions"].as_array().unwrap()
        } else {
            st["Pool"].as_array().unwrap()
        };
        let row = rows
            .iter()
            .find(|x| {
                if !s(r, "PoolKey").is_empty() {
                    s(x, "PoolKey") == s(r, "PoolKey")
                } else {
                    s(x, "Account") == s(r, "Account")
                        && s(x, "Id") == s(r, "SessionId")
                        && (s(r, "RootKey").is_empty() || s(x, "RootKey") == s(r, "RootKey"))
                }
            })
            .ok_or("未找到会话。")?;
        let file = File::open(s(row, "Transcript")).map_err(err)?;
        let (mut last, mut time, mut model) = (Value::Null, String::new(), String::new());
        let mut messages = std::collections::BTreeSet::new();
        for line in BufReader::new(file).lines() {
            let line = line.map_err(err)?;
            if !line.contains("\"usage\"") {
                continue;
            }
            let Ok(v) = serde_json::from_str::<Value>(&line) else {
                continue;
            };
            let u = &v["message"]["usage"];
            if u.is_null() {
                continue;
            }
            last = u.clone();
            time = s(&v, "timestamp").into();
            model = s(&v["message"], "model").into();
            let id = s(&v["message"], "id");
            if !id.is_empty() {
                messages.insert(id.to_owned());
            }
        }
        if last.is_null() {
            return Ok(
                json!({"Found":false,"Message":"此对话没有记录用量字段，无法判断历史缓存使用情况。"}),
            );
        }
        let num = |k: &str| last[k].as_u64().unwrap_or(0);
        let (input, read, write, output) = (
            num("input_tokens"),
            num("cache_read_input_tokens"),
            num("cache_creation_input_tokens"),
            num("output_tokens"),
        );
        let total = input + read + write;
        Ok(
            json!({"Found":true,"Title":row["Title"],"Time":time,"Model":model,"Input":input,"Read":read,"Write":write,"Output":output,"TotalInput":total,"ReadPercent":if total>0{(read as f64*1000.0/total as f64).round()/10.0}else{0.0},"Requests":messages.len()}),
        )
    }
    fn assert_closed(&self) -> Result<()> {
        if self.fixture {
            return Ok(());
        }
        let output = command_hidden("powershell.exe")
            .args([
                "-NoProfile",
                "-Command",
                "if (Get-Process -Name claude -ErrorAction SilentlyContinue) { exit 10 }",
            ])
            .output()
            .map_err(err)?;
        if !output.status.success() {
            return Err("请先从系统托盘完全退出 Claude；不会强制结束会话。".into());
        }
        Ok(())
    }
    fn backup_files(&self) -> Result<Vec<(PathBuf, String)>> {
        let mut out = vec![];
        for (base, label, names) in [
            (
                &self.claude,
                "claude",
                vec![
                    "projects",
                    "file-history",
                    "sessions",
                    "session-env",
                    "tasks",
                    "todos",
                    "plans",
                ],
            ),
            (
                &self.app,
                "desktop",
                vec![
                    "claude-code-sessions",
                    "local-agent-mode-sessions",
                    "rpm",
                    "git-shadow",
                    "blob_storage",
                ],
            ),
        ] {
            for n in names {
                for p in files(&base.join(n))? {
                    let relative = p
                        .strip_prefix(base)
                        .map_err(err)?
                        .to_string_lossy()
                        .replace('\\', "/");
                    out.push((p, format!("{label}/{relative}")));
                }
            }
        }
        let history = self.claude.join("history.jsonl");
        if history.is_file() {
            out.push((history, "claude/history.jsonl".into()));
        }
        out.extend(workspace::third_backup_files(self)?);
        out.sort();
        Ok(out)
    }
    fn backup(&self, reason: &str) -> Result<Value> {
        self.assert_closed()?;
        let id = format!(
            "{}-{:06x}",
            local_now().format("%Y%m%d-%H%M%S"),
            ((now_nanos() as u32) & 0xffffff)
        );
        let dir = self.data.join("backups").join(&id);
        fs::create_dir_all(dir.parent().ok_or("备份目录无效")?).map_err(err)?;
        fs::create_dir(&dir).map_err(err)?;
        let archive = dir.join("conversation-data.zip");
        let mut zip = ZipWriter::new(File::create(&archive).map_err(err)?);
        let mut records = vec![];
        let original = self.backup_files()?;
        for (p, member) in &original {
            let before = hash(p)?;
            let bytes = fs::metadata(p).map_err(err)?.len();
            zip.start_file(
                member,
                FileOptions::default().compression_method(zip::CompressionMethod::Deflated),
            )
            .map_err(err)?;
            std::io::copy(&mut File::open(p).map_err(err)?, &mut zip).map_err(err)?;
            if hash(p)? != before {
                return Err("备份期间原文发生变化，未执行迁移。".into());
            }
            records.push(json!({"Source":p,"Member":member,"Sha256":before,"Bytes":bytes}));
        }
        zip.finish().map_err(err)?.sync_all().map_err(err)?;
        if original != self.backup_files()? {
            return Err("备份文件集合发生变化，未执行迁移。".into());
        }
        let mut z = ZipArchive::new(File::open(&archive).map_err(err)?).map_err(err)?;
        for rec in &records {
            let mut f = z.by_name(s(rec, "Member")).map_err(err)?;
            if hash_reader(&mut f)? != s(rec, "Sha256")
                || hash(Path::new(s(rec, "Source")))? != s(rec, "Sha256")
            {
                return Err("备份完整性校验失败。".into());
            }
        }
        drop(z);
        write_json(
            &dir.join("manifest.json"),
            &json!({"Id":id,"Time":local_now().format("%Y-%m-%d %H:%M").to_string(),"Reason":reason,"Files":records,"Created":[],"Undone":false,"ArchiveSha256":hash(&archive)?}),
        )?;
        Ok(json!({"Id":id,"Path":dir,"Files":records.len(),"Verified":true}))
    }
    fn copy(&self, r: &Value) -> Result<Value> {
        for k in ["Source", "Target", "Org"] {
            uuid(s(r, k))?
        }
        if s(r, "Source") == s(r, "Target") {
            return Err("请选择另一个目标账号。".into());
        }
        self.assert_closed()?;
        let target = self.registry().join(s(r, "Target")).join(s(r, "Org"));
        if !target.is_dir() {
            return Err("目标组织目录不存在。".into());
        }
        let st = self.state()?;
        let rows = st["Sessions"].as_array().unwrap();
        let mut plan = vec![];
        let mut seen = std::collections::BTreeSet::new();
        let mut skip = 0;
        for sid in r["SessionIds"].as_array().ok_or("请选择会话。")? {
            let sid = sid.as_str().ok_or("会话标识无效。")?;
            if !seen.insert(sid.to_owned()) {
                continue;
            }
            let raw = sid.strip_prefix("local_").ok_or("会话标识无效。")?;
            uuid(raw)?;
            let src = rows
                .iter()
                .find(|x| s(x, "Account") == s(r, "Source") && s(x, "Id") == sid)
                .ok_or("源账号中未找到会话。")?;
            if !src["HasTranscript"].as_bool().unwrap_or(false) {
                return Err("所选会话缺少原文，未开始迁移。".into());
            }
            let dest = target.join(format!("{sid}.json"));
            if dest.exists() {
                skip += 1;
                continue;
            }
            if target.join(format!("deleted_{raw}")).exists() {
                return Err("目标账号曾删除此会话，已停止。".into());
            }
            plan.push((src.clone(), dest));
        }
        if plan.is_empty() {
            return Ok(
                json!({"Copied":0,"Skipped":skip,"Message":"所选会话已存在，无需重复迁移。"}),
            );
        }
        let backup = self.backup(&format!("迁移前备份 · {} 条", plan.len()))?;
        self.assert_closed()?;
        let mp = PathBuf::from(s(&backup, "Path")).join("manifest.json");
        let mut manifest = read_json(&mp)?;
        for (src, dest) in plan {
            let mut j = read_json(Path::new(s(&src, "Path")))?;
            let map = j.as_object_mut().ok_or("索引格式无效。")?;
            if map.contains_key("bridgeSessionIds") {
                map.insert("bridgeSessionIds".into(), json!([]));
            }
            map.remove("scheduledTaskId");
            let text = serde_json::to_vec_pretty(&j).map_err(err)?;
            // Journal intent first. create_new prevents overwriting an existing target.
            let expected = format!("{:x}", Sha256::digest(&text));
            manifest["Created"]
                .as_array_mut()
                .unwrap()
                .push(json!({"Path":dest,"Sha256":expected,"Title":src["Title"]}));
            write_json(&mp, &manifest)?;
            let mut f = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&dest)
                .map_err(err)?;
            f.write_all(&text).map_err(err)?;
            f.sync_all().map_err(err)?;
            if read_json(&dest)? != j {
                return Err("新增索引校验失败，请查看备份清单。".into());
            }
        }
        for rec in manifest["Files"].as_array().unwrap() {
            if hash(Path::new(s(rec, "Source")))? != s(rec, "Sha256") {
                return Err("原文件发生变化，请查看备份清单。".into());
            }
        }
        Ok(
            json!({"Copied":manifest["Created"].as_array().unwrap().len(),"Skipped":skip,"Backup":backup["Id"],"Message":format!("迁移完成，新增 {} 条，跳过 {} 条已有记录。备份与原文件校验通过。",manifest["Created"].as_array().unwrap().len(),skip)}),
        )
    }
    fn undo(&self, id: &str) -> Result<Value> {
        if id.len() != 22 || !id.chars().all(|c| c.is_ascii_hexdigit() || c == '-') {
            return Err("备份标识无效。".into());
        }
        self.assert_closed()?;
        let dir = self.data.join("backups").join(id);
        let mp = dir.join("manifest.json");
        let mut m = read_json(&mp)?;
        if m["Undone"].as_bool().unwrap_or(false) {
            return Err("此迁移已撤回。".into());
        }
        let created = m["Created"].as_array().ok_or("备份清单无效。")?;
        if created.is_empty() {
            return Err("此备份没有可撤回的新增索引。".into());
        }
        let root = fs::canonicalize(self.registry()).map_err(err)?;
        let mut moves = vec![];
        for rec in created {
            let p = PathBuf::from(s(rec, "Path"));
            let canonical = fs::canonicalize(&p).map_err(err)?;
            let relative = canonical.strip_prefix(&root).map_err(err)?;
            let c: Vec<_> = relative.components().collect();
            if c.len() != 3 {
                return Err("回退目标超出会话目录。".into());
            }
            uuid(&c[0].as_os_str().to_string_lossy())?;
            uuid(&c[1].as_os_str().to_string_lossy())?;
            let filename = name(&p);
            let raw = filename
                .strip_prefix("local_")
                .and_then(|x| x.strip_suffix(".json"))
                .ok_or("回退文件类型无效。")?;
            uuid(raw)?;
            if hash(&p)? != s(rec, "Sha256") {
                return Err("新增会话已被修改，为保留新数据，停止自动撤回。".into());
            }
            moves.push(p);
        }
        let stash = dir.join("withdrawn-indexes");
        fs::create_dir_all(&stash).map_err(err)?;
        for p in moves {
            let dest = stash.join(name(&p));
            if dest.exists() {
                return Err("回退保存位置已存在。".into());
            }
            fs::rename(p, dest).map_err(err)?;
        }
        m["Undone"] = json!(true);
        write_json(&mp, &m)?;
        Ok(json!(
            "已撤回新增索引；源账号、目标原有记录及全部原文保留。"
        ))
    }
    fn dispatch(&self, r: &Value) -> Result<Value> {
        match s(r, "Action") {
            "state" => self.state(),
            "workspace-plan" => workspace::sharing_plan(self),
            "usage" => self.usage(r),
            "backup" => self.backup("完整备份"),
            "copy" => self.copy(r),
            "undo" => self.undo(s(r, "Id")),
            "label" => {
                uuid(s(r, "Account"))?;
                let label = s(r, "Label").trim();
                if label.is_empty()
                    || label.chars().count() > 120
                    || label.chars().any(char::is_control)
                {
                    return Err("显示名称须为 1–120 个单行字符。".into());
                }
                let p = self.data.join("labels.json");
                let mut labels = opt_json(&p);
                if !labels.is_object() {
                    labels = json!({})
                }
                labels[s(r, "Account")] = json!(label);
                write_json(&p, &labels)?;
                Ok(json!("显示名称已更新。"))
            }
            _ => Err("未知数据操作。".into()),
        }
    }
}
fn run() -> Result<()> {
    let args: Vec<String> = env::args().collect();
    if args.iter().any(|s| s == "--version") {
        println!("claude-switch-core {} (Desktop capture wizard; guarded local account/provider adapter)", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }
    let arg = |n: &str| {
        args.iter()
            .position(|x| x == n)
            .and_then(|i| args.get(i + 1))
            .cloned()
    };
    let request = PathBuf::from(arg("--request").ok_or("--request required")?);
    let response = PathBuf::from(arg("--response").ok_or("--response required")?);
    let exe = env::current_exe().map_err(err)?;
    let base = arg("--base")
        .map(PathBuf::from)
        .unwrap_or_else(|| exe.parent().unwrap().to_owned());
    let kernel = arg("--kernel")
        .map(PathBuf::from)
        .unwrap_or_else(|| base.parent().unwrap_or(&base).to_owned());
    let ctx = Context::new(base.clone(), kernel, arg("--fixture").map(PathBuf::from))?;
    let r = read_json(&request)?;
    if ["copy", "undo"].contains(&s(&r, "Action")) && r["MaintenanceConfirmed"] != true {
        write_json(
            &response,
            &json!({"Ok":false,"Error":"真实数据变更尚未确认，已停止。请先退出 Claude 并确认停机窗口。"}),
        )?;
        return Ok(());
    }
    if ["save", "switch", "provider-switch"].contains(&s(&r, "Action")) {
        if ctx.fixture {
            return Err("测试模式禁止账号操作。".into());
        }
        if r["MaintenanceConfirmed"] != true {
            write_json(
                &response,
                &json!({"Ok":false,"Error":"请先确认已退出 Claude；不会强制结束进程。"}),
            )?;
            return Ok(());
        }
        if let Err(e) = ctx.assert_closed() {
            write_json(&response, &json!({"Ok":false,"Error":e}))?;
            return Ok(());
        }
        fs::create_dir_all(&ctx.data).map_err(err)?;
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(ctx.data.join("operations.lock"))
            .map_err(err)?;
        lock.try_lock_exclusive()
            .map_err(|_| "另一个操作尚未完成，请稍候。".to_owned())?;
        let output = command_hidden(
            workspace::python_path(&base)?
                .to_str()
                .ok_or("Python 路径无效")?,
        )
        .arg(base.join("local_adapter.py"))
        .arg("execute")
        .arg("--request")
        .arg(&request)
        .arg("--response")
        .arg(&response)
        .arg("--data-root")
        .arg(&ctx.data)
        .output()
        .map_err(err)?;
        if !output.status.success() || !response.is_file() {
            return Err("本地适配器未完成，账号未被强制关闭，请查看提示。".into());
        }
        FileExt::unlock(&lock).map_err(err)?;
        return Ok(());
    }
    fs::create_dir_all(&ctx.data).map_err(err)?;
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(ctx.data.join("operations.lock"))
        .map_err(err)?;
    lock.try_lock_exclusive()
        .map_err(|_| "另一个数据操作正在进行，请稍后重试。".to_owned())?;
    let result = match ctx.dispatch(&r) {
        Ok(v) => json!({"Ok":true,"Result":v}),
        Err(e) => json!({"Ok":false,"Error":e}),
    };
    write_json(&response, &result)?;
    FileExt::unlock(&lock).map_err(err)?;
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("{e}");
        std::process::exit(1)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const A: &str = "11111111-1111-4111-8111-111111111111";
    const B: &str = "22222222-2222-4222-8222-222222222222";
    const O: &str = "33333333-3333-4333-8333-333333333333";
    const C: &str = "44444444-4444-4444-8444-444444444444";
    fn fixture() -> (PathBuf, Context) {
        let p = env::temp_dir().join(format!(
            "claude-studio-rust-test-{}-{}",
            std::process::id(),
            now_nanos()
        ));
        fs::create_dir_all(&p).unwrap();
        let c = Context::new(p.clone(), p.clone(), Some(p.clone())).unwrap();
        write_json(
            &c.app.join("config.json"),
            &json!({"lastKnownAccountUuid":A}),
        )
        .unwrap();
        fs::create_dir_all(c.registry().join(B).join(O)).unwrap();
        let transcript = c.claude.join("projects/p").join(format!("{C}.jsonl"));
        fs::create_dir_all(transcript.parent().unwrap()).unwrap();
        fs::write(&transcript,serde_json::to_vec(&json!({"type":"assistant","timestamp":"2026-10-28T00:00:00Z","message":{"id":"r1","usage":{"input_tokens":10,"cache_read_input_tokens":90,"output_tokens":5}}})).unwrap()).unwrap();
        let sid = format!("local_{C}");
        write_json(&c.registry().join(A).join(O).join(format!("{sid}.json")),&json!({"sessionId":sid,"cliSessionId":C,"title":"中文测试对话","cwd":"p","bridgeSessionIds":["old-cloud"],"scheduledTaskId":"old-task","unknown":{"keep":true}})).unwrap();
        (p, c)
    }
    fn cleanup(p: PathBuf) {
        let target = fs::canonicalize(&p).unwrap();
        let temp = fs::canonicalize(env::temp_dir()).unwrap();
        assert_eq!(target.parent(), Some(temp.as_path()));
        assert!(name(&target).starts_with("claude-studio-rust-test-"));
        fs::remove_dir_all(target).unwrap();
    }
    #[test]
    fn rejects_invalid_ids() {
        assert!(uuid("../../unsafe").is_err());
        assert!(uuid(A).is_ok());
    }
    #[test]
    fn additive_copy_backup_and_modified_undo() {
        let (p, c) = fixture();
        let src = c.registry().join(A).join(O).join(format!("local_{C}.json"));
        let before = hash(&src).unwrap();
        let r = json!({"Source":A,"Target":B,"Org":O,"SessionIds":[format!("local_{C}")]});
        let result = c.copy(&r).unwrap();
        assert_eq!(result["Copied"], 1);
        assert_eq!(hash(&src).unwrap(), before);
        let dst = c.registry().join(B).join(O).join(format!("local_{C}.json"));
        let d = read_json(&dst).unwrap();
        assert_eq!(d["bridgeSessionIds"], json!([]));
        assert!(d.get("scheduledTaskId").is_none());
        assert_eq!(d["unknown"], json!({"keep":true}));
        assert_eq!(c.copy(&r).unwrap()["Skipped"], 1);
        let id = s(&result, "Backup");
        let mut altered = d.clone();
        altered["title"] = json!("new data");
        write_json(&dst, &altered).unwrap();
        assert!(c.undo(id).is_err());
        assert!(dst.exists());
        write_json(&dst, &d).unwrap();
        c.undo(id).unwrap();
        assert!(!dst.exists());
        assert!(src.exists());
        cleanup(p);
    }
    #[test]
    fn reads_historical_usage() {
        let (p, c) = fixture();
        let usage = c
            .usage(&json!({"Account":A,"SessionId":format!("local_{C}")}))
            .unwrap();
        assert_eq!(usage["ReadPercent"], 90.0);
        assert_eq!(usage["Input"], 10);
        cleanup(p);
    }
}
