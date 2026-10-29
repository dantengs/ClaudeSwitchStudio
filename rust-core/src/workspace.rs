//! Cross-account local catalog. Reading the pool never copies or rewrites a transcript.
use super::*;
use std::collections::{BTreeMap, BTreeSet};

fn identities(value: &Value, out: &mut BTreeMap<String, BTreeSet<String>>) {
    // An email is only accepted next to its exact account UUID, never by directory proximity.
    if let Some(account) = value.get("oauthAccount") {
        let id = s(account, "accountUuid");
        let email = s(account, "emailAddress").trim();
        if uuid(id).is_ok() && email.contains('@') && !email.contains(char::is_whitespace) {
            out.entry(id.to_owned())
                .or_default()
                .insert(email.to_owned());
        }
    }
}

fn email_map(ctx: &Context) -> BTreeMap<String, BTreeSet<String>> {
    let user = if ctx.fixture {
        ctx.app.parent().unwrap_or(&ctx.app).join("user")
    } else {
        PathBuf::from(env::var("USERPROFILE").unwrap_or_default())
    };
    let mut candidates = vec![user.join(".claude.json"), user.join(".claude.json.backup")];
    for root in [
        ctx.claude.join("backups"),
        ctx.profiles.clone(),
        ctx.app.join("local-agent-mode-sessions"),
    ] {
        if let Ok(all) = files(&root) {
            candidates.extend(all.into_iter().filter(|p| {
                let n = name(p);
                n == ".claude.json" || n == "claude.json" || n.starts_with(".claude.json.backup")
            }));
        }
    }
    let mut out = BTreeMap::new();
    for path in candidates {
        if let Ok(value) = read_json(&path) {
            identities(&value, &mut out);
        }
    }
    out
}

pub(super) fn third_root(ctx: &Context) -> PathBuf {
    if ctx.fixture {
        ctx.app.parent().unwrap().join("desktop-3p")
    } else {
        PathBuf::from(env::var("USERPROFILE").unwrap_or_default()).join("AppData/Local/Claude-3p")
    }
}

pub(super) fn enrich(ctx: &Context, mut state: Value) -> Result<Value> {
    let email = email_map(ctx);
    let confirmed = opt_json(&ctx.data.join("identities.json"));
    for a in state["Accounts"].as_array_mut().unwrap() {
        let id = s(a, "Uuid").to_owned();
        let addresses = email.get(&id);
        let discovered = addresses
            .filter(|e| e.len() == 1)
            .and_then(|e| e.first())
            .cloned()
            .unwrap_or_default();
        let record = &confirmed["Accounts"][&id];
        let manual = s(record, "Email");
        let trusted = s(record, "ConfirmedBy") == "user"
            && manual.contains('@')
            && !manual.contains(char::is_whitespace);
        let address = if trusted {
            manual.to_owned()
        } else {
            discovered.clone()
        };
        a["Email"] = json!(address);
        a["DiscoveredEmail"] = json!(discovered);
        a["EmailSource"] = json!(if trusted {
            "user-confirmed"
        } else {
            "local-oauth-metadata"
        });
        a["EmailStatus"] = json!(if trusted {
            "用户确认的邮箱对应关系"
        } else if addresses.is_some_and(|e| e.len() > 1) {
            "本地身份记录冲突"
        } else if address.is_empty() {
            "未找到匹配的邮箱"
        } else {
            "UUID 精确匹配"
        });
        a["EmailDetail"] = json!(if address.is_empty() {
            s(a, "EmailStatus").to_owned()
        } else {
            format!("{} · {}", address, s(a, "EmailStatus"))
        });
        a["RootKey"] = json!("official");
        a["AccountKey"] = json!(format!("official|{id}"));
        a["Kind"] = json!("官方账号");
        if !address.is_empty() && !a["HasCustomName"].as_bool().unwrap_or(false) {
            a["Name"] = json!(address);
            a["Initial"] = json!(address
                .chars()
                .next()
                .unwrap_or('A')
                .to_uppercase()
                .to_string());
        }
        if s(
            &opt_json(&ctx.app.join("claude_desktop_config.json")),
            "deploymentMode",
        ) == "3p"
        {
            a["IsCurrent"] = json!(false);
        }
        a["CanSave"] = a["IsCurrent"].clone();
        a["SwitchKind"] = json!("official");
        a["SwitchId"] = a["Profile"].clone();
        let current = a["IsCurrent"].as_bool().unwrap_or(false);
        let saved = a["Saved"].as_bool().unwrap_or(false);
        a["CanSwitch"] = json!(saved && !current);
        a["PrimaryAction"] = json!(if current {
            "capture"
        } else if saved {
            "switch"
        } else {
            "login-guide"
        });
        a["ActionLabel"] = json!(if current && saved {
            "更新登录状态"
        } else if current {
            "收录当前登录"
        } else if saved {
            "切换账号"
        } else {
            "添加登录"
        });
        a["ActionEnabled"] = json!(true);
        a["Status"] = json!(if current {
            "桌面版当前身份"
        } else if saved {
            "已收录"
        } else {
            "仅有历史记录"
        });
        a["ProfileStatus"] = json!(if saved {
            "本地登录快照已保存 · 有效性需在 Claude 中确认"
        } else {
            "尚未收录桌面版登录 · Claude Code 登录独立"
        });
        let saved_label = match (saved, s(a, "SavedAt")) {
            (true, "") => "已收录".to_owned(),
            (true, at) => format!("收录于 {at}"),
            _ => "未收录".to_owned(),
        };
        a["SavedLabel"] = json!(saved_label);
    }
    for row in state["Sessions"].as_array_mut().unwrap() {
        row["RootKey"] = json!("official");
        row["AccountKey"] = json!(format!("official|{}", s(row, "Account")));
    }
    let third = third_root(ctx);
    if third.join("config.json").is_file() {
        let child = Context {
            app: third.clone(),
            claude: ctx.claude.clone(),
            profiles: ctx.profiles.clone(),
            data: ctx.data.clone(),
            kernel: ctx.kernel.clone(),
            fixture: ctx.fixture,
        };
        let mut extra = child.state_base()?;
        let mut actual: BTreeSet<String> =
            dirs(&child.registry())?.iter().map(|p| name(p)).collect();
        let cfg = opt_json(&third.join("config.json"));
        let live = s(&cfg, "lastKnownAccountUuid");
        if uuid(live).is_ok() {
            actual.insert(live.to_owned());
        }
        for a in extra["Accounts"].as_array_mut().unwrap() {
            let id = s(a, "Uuid").to_owned();
            if !actual.contains(&id) {
                continue;
            }
            a["RootKey"] = json!("third-party");
            a["AccountKey"] = json!(format!("third-party|{id}"));
            a["Kind"] = json!("第三方本地身份");
            a["Email"] = json!("");
            a["EmailStatus"] = json!("提供商身份不一定有邮箱");
            a["Name"] = json!("第三方本地工作区");
            a["Initial"] = json!("3P");
            a["CanSwitch"] = json!(false);
            a["CanSave"] = json!(false);
            a["IsCurrent"] = json!(false);
            a["Status"] = json!("本地工作区");
            a["ProfileStatus"] = json!("共享原文 · 认证独立");
            a["SwitchKind"] = json!("workspace");
            a["PrimaryAction"] = json!("providers");
            a["ActionLabel"] = json!("选择提供商");
            a["ActionEnabled"] = json!(true);
            state["Accounts"].as_array_mut().unwrap().push(a.clone());
        }
        for row in extra["Sessions"].as_array_mut().unwrap() {
            row["RootKey"] = json!("third-party");
            row["AccountKey"] = json!(format!("third-party|{}", s(row, "Account")));
            state["Sessions"].as_array_mut().unwrap().push(row.clone());
        }
    }
    let names: BTreeMap<String, String> = state["Accounts"]
        .as_array()
        .unwrap()
        .iter()
        .map(|a| (s(a, "AccountKey").to_owned(), s(a, "Name").to_owned()))
        .collect();
    for row in state["Sessions"].as_array_mut().unwrap() {
        let label = names
            .get(s(row, "AccountKey"))
            .cloned()
            .unwrap_or_else(|| s(row, "AccountName").to_owned());
        row["AccountName"] = json!(label);
    }
    let mut pool_input = state["Sessions"].as_array().unwrap().clone();
    let used: BTreeSet<String> = pool_input
        .iter()
        .map(|r| s(r, "CliId").to_owned())
        .collect();
    let projects = ctx.claude.join("projects");
    for (id, path) in ctx.transcripts()? {
        if used.contains(&id) || path.parent().and_then(Path::parent) != Some(projects.as_path()) {
            continue;
        }
        let bytes = fs::metadata(&path).map_err(err)?.len();
        pool_input.push(json!({"Account":"","AccountKey":"unregistered","RootKey":"unregistered","Id":format!("local_{id}"),"CliId":id,"Title":format!("未注册原文 {}",short(&id)),"Cwd":name(path.parent().unwrap()),"When":"—","LastAt":0,"Transcript":path,"HasTranscript":true,"Health":"未注册原文","Size":format!("{:.1} MB",bytes as f64/1048576.0),"AccountName":"未注册原文"}));
    }
    let pool = pool_rows(&pool_input);
    state["Pool"] = json!(pool);
    state["PoolCount"] = json!(pool.len());
    state["RegisteredPoolCount"] = json!(pool.iter().filter(|r| r["IsRegistered"] == true).count());
    state["IndexCount"] = json!(state["Sessions"].as_array().unwrap().len());
    state["Memory"] = json!(memory_catalog(ctx)?);
    state["Providers"] = if ctx.fixture {
        opt_json(&ctx.data.join("provider-fixture.json"))
    } else {
        provider_catalog(ctx).unwrap_or_else(|e| json!({"Providers":[],"Warning":e}))
    };
    state["Backend"] = json!(format!("Rust {} · 本地共享池", env!("CARGO_PKG_VERSION")));
    Ok(state)
}

pub(super) fn pool_rows(rows: &[Value]) -> Vec<Value> {
    let mut groups: BTreeMap<String, Vec<&Value>> = BTreeMap::new();
    let mut slots: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for row in rows {
        let transcript = s(row, "Transcript");
        let key = if !transcript.is_empty() {
            format!("{}|{}", s(row, "CliId"), transcript.to_lowercase())
        } else {
            format!(
                "missing|{}|{}|{}",
                s(row, "RootKey"),
                s(row, "Id"),
                s(row, "CliId")
            )
        };
        groups.entry(key).or_default().push(row);
        slots
            .entry(s(row, "Id").to_owned())
            .or_default()
            .insert(s(row, "CliId").to_owned());
    }
    let mut out = vec![];
    for (key, mut group) in groups {
        group.sort_by_key(|r| std::cmp::Reverse(r["LastAt"].as_i64().unwrap_or(0)));
        let mut chosen = (*group[0]).clone();
        let origins: BTreeSet<_> = group.iter().map(|r| s(r, "AccountName")).collect();
        let branch = slots.get(s(&chosen, "Id")).is_some_and(|s| s.len() > 1);
        chosen["PoolKey"] = json!(key);
        chosen["IsRegistered"] = json!(!s(&chosen, "Account").is_empty());
        chosen["Origins"] = json!(origins.into_iter().collect::<Vec<_>>().join(" / "));
        let keys: BTreeSet<_> = group.iter().map(|r| s(r, "AccountKey")).collect();
        chosen["OriginKeys"] = json!(keys.into_iter().collect::<Vec<_>>());
        chosen["References"] = json!(group.len());
        chosen["BranchConflict"] = json!(branch);
        chosen["Health"] = json!(if branch {
            "保留分支"
        } else if chosen["HasTranscript"].as_bool().unwrap_or(false) {
            "共享原文"
        } else {
            "缺少原文"
        });
        out.push(chosen);
    }
    out.sort_by_key(|r| std::cmp::Reverse(r["LastAt"].as_i64().unwrap_or(0)));
    out
}

fn memory_catalog(ctx: &Context) -> Result<Vec<Value>> {
    let mut out = vec![];
    for project in dirs(&ctx.claude.join("projects"))? {
        let root = project.join("memory");
        let entries = files(&root)?;
        if entries.is_empty() {
            continue;
        }
        out.push(json!({"Project":name(&project),"Path":root,"Files":entries.len(),"Status":"本地项目记忆"}));
    }
    Ok(out)
}
pub(super) fn third_backup_files(ctx: &Context) -> Result<Vec<(PathBuf, String)>> {
    let root = third_root(ctx);
    let mut out = vec![];
    for directory in [
        "claude-code-sessions",
        "local-agent-mode-sessions",
        "rpm",
        "blob_storage",
    ] {
        for path in files(&root.join(directory))? {
            let member = format!(
                "desktop-3p/{}",
                path.strip_prefix(&root)
                    .map_err(err)?
                    .to_string_lossy()
                    .replace('\\', "/")
            );
            out.push((path, member));
        }
    }
    Ok(out)
}

pub(super) fn python_path(base: &Path) -> Result<PathBuf> {
    python_path_in(
        base,
        Path::new(&env::var("USERPROFILE").unwrap_or_default()),
    )
}

fn python_path_in(base: &Path, user_profile: &Path) -> Result<PathBuf> {
    // Use the exact bundled executable rather than PATH; development checkouts may
    // still use an existing per-user Python installation when no runtime is bundled.
    let bundled = base.join("runtime/python/python.exe");
    if bundled.is_file() {
        return Ok(bundled);
    }
    let root = user_profile.join("AppData/Local/Programs/Python");
    let mut candidates = dirs(&root)?;
    candidates.sort();
    for p in candidates.into_iter().rev() {
        let exe = p.join("python.exe");
        if exe.is_file() {
            return Ok(exe);
        }
    }
    Err("未找到内置 Python 运行时或本机 Python 3，请修复安装后重试。".into())
}
fn provider_catalog(ctx: &Context) -> Result<Value> {
    let base = ctx.data.parent().ok_or("无效的应用目录")?;
    let output = command_hidden(python_path(base)?.to_str().ok_or("Python 路径无效")?)
        .arg(base.join("local_adapter.py"))
        .arg("catalog")
        .arg("--data-root")
        .arg(&ctx.data)
        .output()
        .map_err(err)?;
    if !output.status.success() {
        return Err("CC Switch 提供商读取失败，账号和对话仍可使用。".into());
    }
    serde_json::from_slice(&output.stdout).map_err(err)
}

/// Read-only adapter catalogs (Codex accounts, Codex processes, MCP servers, skills).
/// Test fixtures answer from data/<action>-fixture.json and never inspect the real machine.
pub(super) fn adapter_catalog(ctx: &Context, action: &str) -> Result<Value> {
    if ctx.fixture {
        return Ok(opt_json(&ctx.data.join(format!("{action}-fixture.json"))));
    }
    let data = ctx.data.to_string_lossy().into_owned();
    match action {
        "codex-processes" => adapter(ctx, &["processes", "--app", "codex"]),
        "codex-state" => adapter(ctx, &["codex-catalog", "--data-root", &data]),
        "mcp-state" => adapter(ctx, &["mcp-catalog", "--data-root", &data]),
        "skills-state" => adapter(ctx, &["skills-catalog", "--data-root", &data]),
        "vendor-state" => adapter(ctx, &["vendor-catalog", "--data-root", &data]),
        "cloud-state" => adapter(ctx, &["cloud-catalog", "--data-root", &data]),
        _ => Err("未知的只读目录。".into()),
    }
}

/// Runs a local_adapter.py subcommand that prints one JSON object (with "Error" on failure).
pub(super) fn adapter(ctx: &Context, args: &[&str]) -> Result<Value> {
    let base = ctx.data.parent().ok_or("无效的应用目录")?;
    let output = command_hidden(python_path(base)?.to_str().ok_or("Python 路径无效")?)
        .arg(base.join("local_adapter.py"))
        .args(args)
        .output()
        .map_err(err)?;
    let value: Value = serde_json::from_slice(&output.stdout)
        .map_err(|_| "无法读取 Claude 进程信息，未修改任何文件。".to_owned())?;
    if !output.status.success() {
        let message = s(&value, "Error");
        return Err(if message.is_empty() { "本地适配器未完成，未修改任何文件。".into() } else { message.into() });
    }
    Ok(value)
}

pub(super) fn claude_processes(ctx: &Context) -> Result<Value> {
    if ctx.fixture {
        return Ok(opt_json(&ctx.data.join("process-fixture.json")));
    }
    adapter(ctx, &["processes"])
}

pub(super) fn sharing_plan(ctx: &Context) -> Result<Value> {
    let state = ctx.state()?;
    let mut leaves = vec![];
    let mut guarded = vec![];
    for (key, app) in [
        ("official", ctx.app.clone()),
        ("third-party", third_root(ctx)),
    ] {
        for account in dirs(&app.join("claude-code-sessions"))? {
            for org in dirs(&account)? {
                let all = files(&org)?;
                let other: Vec<_> = all
                    .iter()
                    .filter(|p| !name(p).starts_with("local_"))
                    .map(|p| name(p))
                    .collect();
                if !other.is_empty() {
                    guarded.push(json!({"RootKey":key,"Account":name(&account),"Org":name(&org),"Files":other}));
                }
                leaves.push(json!({"RootKey":key,"Path":org,"Account":name(&account),"Org":name(&org),"Indexes":all.iter().filter(|p|name(p).starts_with("local_")&&name(p).ends_with(".json")).count()}));
            }
        }
    }
    Ok(
        json!({"Mode":"read-only-plan","Mutations":0,"OriginalIndexes":state["IndexCount"],"UniqueConversations":state["RegisteredPoolCount"],"UnregisteredTranscripts":state["PoolCount"].as_u64().unwrap_or(0)-state["RegisteredPoolCount"].as_u64().unwrap_or(0),"Leaves":leaves,"AccountLocalState":guarded,"Memory":state["Memory"],"PhysicalLinkEnabled":false,"Message":"统一原文池和去重目录可直接使用。整目录联接还会共享计划任务、删除标记和账号状态，必须先在停机备份后做单会话真机验证，当前未启用。"}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bundled_python_wins_and_development_installation_is_the_fallback() {
        let root = env::temp_dir().join(format!(
            "claude-studio-python-runtime-{}-{}",
            std::process::id(),
            now_nanos()
        ));
        fs::create_dir(&root).unwrap();
        let base = root.join("application");
        let user = root.join("user");
        let bundled = base.join("runtime/python/python.exe");
        let system = user.join("AppData/Local/Programs/Python/Python314/python.exe");
        fs::create_dir_all(bundled.parent().unwrap()).unwrap();
        fs::create_dir_all(system.parent().unwrap()).unwrap();
        fs::write(&bundled, b"bundled fixture").unwrap();
        fs::write(&system, b"development fixture").unwrap();
        assert_eq!(python_path_in(&base, &user).unwrap(), bundled);
        fs::remove_file(&bundled).unwrap();
        assert_eq!(python_path_in(&base, &user).unwrap(), system);
        fs::remove_file(&system).unwrap();
        assert!(python_path_in(&base, &user).is_err());
        let resolved = fs::canonicalize(&root).unwrap();
        assert_eq!(
            resolved.parent(),
            Some(fs::canonicalize(env::temp_dir()).unwrap().as_path())
        );
        assert!(name(&resolved).starts_with("claude-studio-python-runtime-"));
        fs::remove_dir_all(resolved).unwrap();
    }
    #[test]
    fn desktop_actions_distinguish_history_current_and_saved_login() {
        let root = env::temp_dir().join(format!(
            "claude-studio-actions-{}-{}",
            std::process::id(),
            now_nanos()
        ));
        fs::create_dir(&root).unwrap();
        let ctx = Context::new(root.clone(), root.clone(), Some(root.clone())).unwrap();
        let a = "11111111-1111-4111-8111-111111111111";
        let b = "22222222-2222-4222-8222-222222222222";
        write_json(
            &ctx.app.join("config.json"),
            &json!({"lastKnownAccountUuid":a}),
        )
        .unwrap();
        fs::create_dir_all(ctx.registry().join(b)).unwrap();
        let state = ctx.state().unwrap();
        let row = |id: &str| {
            state["Accounts"]
                .as_array()
                .unwrap()
                .iter()
                .find(|v| s(v, "Uuid") == id)
                .unwrap()
        };
        assert_eq!(row(a)["PrimaryAction"], "capture");
        assert_eq!(row(b)["PrimaryAction"], "login-guide");
        assert_eq!(row(b)["ActionEnabled"], true);
        let profile = ctx.profiles.join(format!("desktop-{b}"));
        for folder in ["Network", "Local Storage", "Session Storage"] {
            fs::create_dir_all(profile.join(folder)).unwrap();
        }
        write_json(&profile.join("Local State"), &json!({})).unwrap();
        write_json(
            &profile.join("config.json"),
            &json!({"lastKnownAccountUuid":b,"oauth:tokenCacheV2":"encrypted-fixture"}),
        )
        .unwrap();
        write_json(
            &profile.join("studio-profile.json"),
            &json!({"Account":b,"SavedAt":"20261028-174826-644930"}),
        )
        .unwrap();
        let state = ctx.state().unwrap();
        let saved = state["Accounts"]
            .as_array()
            .unwrap()
            .iter()
            .find(|v| s(v, "Uuid") == b)
            .unwrap();
        assert_eq!(saved["PrimaryAction"], "switch");
        assert_eq!(saved["SavedLabel"], "收录于 2026-10-28 17:48");
        assert_eq!(
            claude_processes(&ctx).unwrap(),
            Value::Null,
            "fixture mode must not inspect real processes"
        );
        write_json(
            &ctx.app.join("claude_desktop_config.json"),
            &json!({"deploymentMode":"3p"}),
        )
        .unwrap();
        let state = ctx.state().unwrap();
        let current = state["Accounts"]
            .as_array()
            .unwrap()
            .iter()
            .find(|v| s(v, "Uuid") == a)
            .unwrap();
        assert_eq!(current["IsCurrent"], false);
        assert_eq!(current["PrimaryAction"], "login-guide");
        let resolved = fs::canonicalize(&root).unwrap();
        assert_eq!(
            resolved.parent(),
            Some(fs::canonicalize(env::temp_dir()).unwrap().as_path())
        );
        assert!(name(&resolved).starts_with("claude-studio-actions-"));
        fs::remove_dir_all(resolved).unwrap();
    }
    #[test]
    fn user_confirmed_identity_does_not_rewrite_discovered_metadata() {
        let root = env::temp_dir().join(format!(
            "claude-studio-identity-{}-{}",
            std::process::id(),
            now_nanos()
        ));
        fs::create_dir(&root).unwrap();
        let ctx = Context::new(root.clone(), root.clone(), Some(root.clone())).unwrap();
        let id = "11111111-1111-4111-8111-111111111111";
        write_json(
            &ctx.app.join("config.json"),
            &json!({"lastKnownAccountUuid":id}),
        )
        .unwrap();
        let source = root.join("user/.claude.json");
        write_json(
            &source,
            &json!({"oauthAccount":{"accountUuid":id,"emailAddress":"old@example.test"}}),
        )
        .unwrap();
        let before = hash(&source).unwrap();
        write_json(
            &ctx.data.join("identities.json"),
            &json!({"Accounts":{id:{"Email":"confirmed@example.test","ConfirmedBy":"user"}}}),
        )
        .unwrap();
        let state = ctx.state().unwrap();
        let account = &state["Accounts"][0];
        assert_eq!(account["Email"], "confirmed@example.test");
        assert_eq!(account["DiscoveredEmail"], "old@example.test");
        assert_eq!(account["EmailSource"], "user-confirmed");
        assert_eq!(account["Name"], "confirmed@example.test");
        write_json(
            &ctx.data.join("labels.json"),
            &json!({id:"研究账号 + alias"}),
        )
        .unwrap();
        let labeled = ctx.state().unwrap();
        assert_eq!(labeled["Accounts"][0]["Name"], "研究账号 + alias");
        assert_eq!(labeled["Accounts"][0]["Email"], "confirmed@example.test");
        assert!(s(&labeled["Accounts"][0], "EmailDetail").contains("confirmed@example.test"));
        write_json(&ctx.data.join("labels.json"), &json!({})).unwrap();
        let profile = ctx.profiles.join(format!("desktop-{id}"));
        write_json(
            &profile.join("config.json"),
            &json!({"lastKnownAccountUuid":id}),
        )
        .unwrap();
        let fallback = ctx.state().unwrap();
        assert_eq!(fallback["Accounts"][0]["Name"], "confirmed@example.test");
        fs::write(profile.join("label.txt"), "profile-alias@example.test").unwrap();
        let profile_labeled = ctx.state().unwrap();
        assert_eq!(
            profile_labeled["Accounts"][0]["Name"],
            "profile-alias@example.test"
        );
        assert_eq!(
            profile_labeled["Accounts"][0]["Email"],
            "confirmed@example.test"
        );
        assert_eq!(hash(&source).unwrap(), before);
        let resolved = fs::canonicalize(&root).unwrap();
        assert_eq!(
            resolved.parent(),
            Some(fs::canonicalize(env::temp_dir()).unwrap().as_path())
        );
        assert!(name(&resolved).starts_with("claude-studio-identity-"));
        fs::remove_dir_all(resolved).unwrap();
    }
    #[test]
    fn deduplicates_references_but_keeps_forks() {
        let rows = vec![
            json!({"Id":"same-slot","CliId":"one","Transcript":"C:/one.jsonl","LastAt":1,"AccountName":"A","HasTranscript":true}),
            json!({"Id":"same-slot","CliId":"one","Transcript":"C:/one.jsonl","LastAt":2,"AccountName":"B","HasTranscript":true}),
            json!({"Id":"same-slot","CliId":"two","Transcript":"C:/two.jsonl","LastAt":3,"AccountName":"B","HasTranscript":true}),
        ];
        let pool = pool_rows(&rows);
        assert_eq!(pool.len(), 2);
        assert!(pool.iter().all(|r| r["BranchConflict"] == true));
        assert_eq!(pool[1]["References"], 2);
    }
    #[test]
    fn matches_email_only_with_its_uuid() {
        let mut m = BTreeMap::new();
        identities(
            &json!({"emailAddress":"wrong@sample.test","accountUuid":"11111111-1111-4111-8111-111111111111"}),
            &mut m,
        );
        assert!(m.is_empty());
        identities(
            &json!({"oauthAccount":{"accountUuid":"11111111-1111-4111-8111-111111111111","emailAddress":"matched@sample.test"}}),
            &mut m,
        );
        assert_eq!(m.len(), 1);
    }
}
