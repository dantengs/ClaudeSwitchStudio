//! Token usage statistics across accounts, read from local transcripts only.
//!
//! Claude Code writes one transcript line per content block, repeating the same usage, so each
//! request is counted once by (message id, request id). A Desktop request is attributed to the
//! account that was active at its timestamp, taken from this tool's switch history; requests
//! older than any recorded switch fall back to the account that owns the conversation index.
use super::*;
use std::collections::{BTreeMap, HashMap, HashSet};

const CLI: &str = "cli";
const UNKNOWN: &str = "unknown";

#[derive(Default, Clone)]
struct Tally {
    requests: u64,
    input: u64,
    output: u64,
    read: u64,
    write: u64,
    cost: f64,
    unpriced: bool,
}
impl Tally {
    fn add(&mut self, usage: &Value, price: Option<&Value>) {
        let n = |k: &str| usage[k].as_u64().unwrap_or(0);
        let (input, output, read, write) = (
            n("input_tokens"),
            n("output_tokens"),
            n("cache_read_input_tokens"),
            n("cache_creation_input_tokens"),
        );
        self.add_cost(usage, price.map(|p| request_cost(usage, p, input, output, read, write)));
    }
    /// Counts one request in Claude's field names; cost is None when its model has no price.
    fn add_cost(&mut self, usage: &Value, cost: Option<f64>) {
        let n = |k: &str| usage[k].as_u64().unwrap_or(0);
        self.requests += 1;
        self.input += n("input_tokens");
        self.output += n("output_tokens");
        self.read += n("cache_read_input_tokens");
        self.write += n("cache_creation_input_tokens");
        match cost {
            Some(c) => self.cost += c,
            None => self.unpriced = true,
        }
    }
    fn total(&self) -> u64 {
        self.input + self.output + self.read + self.write
    }
    fn json(&self, all: u64, priced: bool) -> Value {
        let prompt = self.input + self.read + self.write;
        json!({"Requests":self.requests,"Input":self.input,"Output":self.output,"CacheRead":self.read,"CacheWrite":self.write,
            "Total":self.total(),"TotalText":tokens(self.total()),"InputText":tokens(self.input),"OutputText":tokens(self.output),
            "CacheReadText":tokens(self.read),"CacheWriteText":tokens(self.write),
            "CacheHitPercent":if prompt>0{(self.read as f64*1000.0/prompt as f64).round()/10.0}else{0.0},
            "SharePercent":if all>0{(self.total() as f64*1000.0/all as f64).round()/10.0}else{0.0},
            "CostText":if !priced{"未配置价格".to_owned()}else if self.unpriced{format!("${:.2}+",self.cost)}else{format!("${:.2}",self.cost)}})
    }
}

/// One request at the official rules: 5-minute and 1-hour cache writes priced apart, a higher tier
/// for long prompts where the model has one, fast-mode rates (cache rates scale with the fast input
/// rate), 1.1x for US-only inference and half price for batch.
fn request_cost(usage: &Value, p: &Value, input: u64, output: u64, read: u64, write: u64) -> f64 {
    let prompt = input + read + write;
    let tier = match p.get("LongPrompt") {
        Some(long) if prompt > long["Threshold"].as_u64().unwrap_or(u64::MAX) => long,
        _ => p,
    };
    let rate = |k: &str| tier[k].as_f64().unwrap_or(0.0);
    let base = rate("Input");
    let (mut input_rate, mut output_rate, mut scale) = (base, rate("Output"), 1.0);
    if s(usage, "speed") == "fast" {
        if let Some(fast) = p.get("Fast") {
            input_rate = fast["Input"].as_f64().unwrap_or(base);
            output_rate = fast["Output"].as_f64().unwrap_or(output_rate);
            scale = if base > 0.0 { input_rate / base } else { 1.0 };
        }
    }
    let one_hour = usage["cache_creation"]["ephemeral_1h_input_tokens"].as_u64().unwrap_or(0).min(write);
    let five_minute = write - one_hour; // Writes without a breakdown count as 5-minute writes.
    let write5 = if tier.get("CacheWrite5m").is_some() { rate("CacheWrite5m") } else { rate("CacheWrite") };
    let write1 = if tier.get("CacheWrite1h").is_some() { rate("CacheWrite1h") } else { write5 };
    let mut cost = (input as f64 * input_rate
        + output as f64 * output_rate
        + (read as f64 * rate("CacheRead") + five_minute as f64 * write5 + one_hour as f64 * write1) * scale)
        / 1_000_000.0;
    if s(usage, "inference_geo") == "us" {
        cost *= 1.1;
    }
    if s(usage, "service_tier") == "batch" {
        cost *= 0.5;
    }
    cost
}

/// One Codex response at OpenAI's Standard prices. OpenAI's input_tokens include cached and
/// cache-write tokens; prompts over the model's threshold (272K) use its long-context row.
/// Cache writes use the 30-minute rate; models without cache columns bill them as plain input.
fn openai_cost(usage: &Value, p: &Value) -> f64 {
    let n = |k: &str| usage[k].as_u64().unwrap_or(0) as f64;
    let (input, cached, written, output) = (n("input_tokens"), n("cached_input_tokens"), n("cache_write_input_tokens"), n("output_tokens"));
    let tier = match p.get("LongPrompt") {
        Some(long) if input > long["Threshold"].as_f64().unwrap_or(f64::MAX) => long,
        _ => p,
    };
    let rate = |k: &str| tier[k].as_f64();
    let base = rate("Input").unwrap_or(0.0);
    ((input - cached - written).max(0.0) * base
        + cached * rate("CacheRead").unwrap_or(base)
        + written * rate("CacheWrite30m").unwrap_or(base)
        + output * rate("Output").unwrap_or(0.0))
        / 1_000_000.0
}

/// Official prices bundled with the program, the refreshed copy in data/, then the user's overrides.
fn load_prices(ctx: &Context, refresh: bool) -> (Value, String) {
    load_vendor_prices(ctx, refresh, "pricing-official.json", &[], "官方价格")
}

fn load_openai_prices(ctx: &Context, refresh: bool) -> (Value, String) {
    load_vendor_prices(ctx, refresh, "pricing-openai.json", &["--app", "codex"], "OpenAI 官方价格")
}

fn load_vendor_prices(ctx: &Context, refresh: bool, file: &str, extra: &[&str], title: &str) -> (Value, String) {
    let fetched_path = ctx.data.join(file);
    let stale = fs::metadata(&fetched_path)
        .and_then(|m| m.modified())
        .map(|t| t.elapsed().map(|e| e.as_secs() > 7 * 86400).unwrap_or(true))
        .unwrap_or(true);
    let mut note = String::new();
    if !ctx.fixture && (refresh || stale) {
        let data = ctx.data.to_string_lossy().into_owned();
        let mut args = vec!["pricing-update", "--data-root", data.as_str()];
        args.extend_from_slice(extra);
        if let Err(e) = super::workspace::adapter(ctx, &args) {
            note = format!("（本次未能更新{title}：{e}）");
        }
    }
    let base = ctx.data.parent().map(|b| b.join(file)).unwrap_or_default();
    let mut models = serde_json::Map::new();
    let mut source = String::new();
    for path in [base, fetched_path] {
        let file = opt_json(&path);
        if let Some(m) = file["Models"].as_object() {
            models.extend(m.clone());
            source = format!("{title} · 更新于 {}", s(&file, "FetchedAt"));
        }
    }
    // One custom file for both vendors; Claude and OpenAI model IDs never collide.
    if let Some(custom) = opt_json(&ctx.data.join("pricing.json"))["Models"].as_object() {
        if !custom.is_empty() {
            models.extend(custom.clone());
            source += " · 含自定义价格";
        }
    }
    (json!({"Models":models}), source + &note)
}

/// Longest priced model ID the request's model equals or extends (dated IDs share their family's price).
fn price_for<'a>(pricing: &'a Value, model: &str) -> Option<&'a Value> {
    pricing["Models"].as_object()?.iter()
        .filter(|(id, _)| model == id.as_str() || model.starts_with(&format!("{id}-")))
        .max_by_key(|(id, _)| id.len())
        .map(|(_, v)| v)
}

fn tokens(n: u64) -> String {
    match n {
        0..=999 => n.to_string(),
        1_000..=999_999 => format!("{:.1}K", n as f64 / 1e3),
        1_000_000..=999_999_999 => format!("{:.2}M", n as f64 / 1e6),
        _ => format!("{:.2}B", n as f64 / 1e9),
    }
}

fn beijing() -> FixedOffset {
    FixedOffset::east_opt(8 * 3600).unwrap()
}

/// Adapter stamps look like 20261028-174826-644930 (Beijing time).
fn stamp_millis(stamp: &str) -> Option<i64> {
    let text = stamp.get(0..15)?;
    let naive = chrono::NaiveDateTime::parse_from_str(text, "%Y%m%d-%H%M%S").ok()?;
    Some(naive.and_local_timezone(beijing()).single()?.timestamp_millis())
}

/// (time, key) points where an identity is known to be active. Keys: account UUID, "3p:<id>", or "".
fn timeline(ctx: &Context) -> Vec<(i64, String)> {
    let mut events = vec![];
    for dir in dirs(&ctx.data.join("private-auth-backups")).unwrap_or_default() {
        let record = opt_json(&dir.join("transaction.json"));
        if s(&record, "Status") != "committed" {
            continue;
        }
        let dir_name = name(&dir);
        let Some(time) = stamp_millis(&dir_name) else { continue };
        if dir_name.ends_with("-switch-account") || dir_name.ends_with("-save-account") {
            if uuid(s(&record, "Account")).is_ok() {
                events.push((time, s(&record, "Account").to_owned()));
            }
        } else if dir_name.ends_with("-login-new") {
            events.push((time, String::new()));
        }
    }
    // Written by the adapter after each completed switch, provider change or capture.
    if let Some(rows) = opt_json(&ctx.data.join("account-timeline.json")).as_array() {
        for row in rows {
            if let Some(time) = row["Time"].as_i64() {
                let key = if s(row, "Mode") == "3p" {
                    format!("3p:{}", s(row, "Provider"))
                } else {
                    s(row, "Account").to_owned()
                };
                events.push((time, key));
            }
        }
    }
    events.sort();
    events
}

/// Conversation owner by CLI session id, ignoring indexes this tool copied while syncing lists.
fn owners(ctx: &Context) -> HashMap<String, String> {
    let mut copies = HashSet::new();
    for journal in files(&ctx.data.join("conversation-sync")).unwrap_or_default() {
        for entry in opt_json(&journal)["Created"].as_array().cloned().unwrap_or_default() {
            copies.insert(s(&entry, "Path").to_lowercase());
        }
    }
    let mut found: HashMap<String, (i64, String)> = HashMap::new();
    let roots = [(ctx.registry(), false), (super::workspace::third_root(ctx).join("claude-code-sessions"), true)];
    for (registry, third) in roots {
        for account in dirs(&registry).unwrap_or_default() {
            for org in dirs(&account).unwrap_or_default() {
                for path in files(&org).unwrap_or_default() {
                    let file = name(&path);
                    if !file.starts_with("local_") || !file.ends_with(".json")
                        || copies.contains(&path.to_string_lossy().to_lowercase())
                    {
                        continue;
                    }
                    let index = opt_json(&path);
                    let created = index["createdAt"].as_i64().unwrap_or(i64::MAX);
                    let key = if third { "3p:".to_owned() } else { name(&account) };
                    let slot = found.entry(s(&index, "cliSessionId").to_owned()).or_insert((created, key.clone()));
                    if created < slot.0 {
                        *slot = (created, key);
                    }
                }
            }
        }
    }
    found.into_iter().map(|(cli, (_, key))| (cli, key)).collect()
}

fn codex_home(ctx: &Context) -> PathBuf {
    if ctx.fixture {
        return ctx.app.parent().unwrap_or(&ctx.app).join("codex");
    }
    env::var("CODEX_HOME").map(PathBuf::from).unwrap_or_else(|_| PathBuf::from(env::var("USERPROFILE").unwrap_or_default()).join(".codex"))
}

/// Codex sessions name the account that created them, so no switch history is needed. Each
/// response is logged once as token_usage_record (deduplicated by response_id); older sessions
/// without those records fall back to the per-turn token_count snapshots.
fn codex_usage(ctx: &Context, since: i64, hours: i64, pricing: &Value) -> Result<(Tally, BTreeMap<String, Tally>, BTreeMap<String, Tally>, BTreeMap<String, Tally>, HashMap<String, (i64, Value)>, i64)> {
    let home = codex_home(ctx);
    let (mut total, mut by_account, mut by_model, mut by_day) =
        (Tally::default(), BTreeMap::<String, Tally>::new(), BTreeMap::<String, Tally>::new(), BTreeMap::<String, Tally>::new());
    let mut quota: HashMap<String, (i64, Value)> = HashMap::new();
    let mut seen = HashSet::new();
    let mut first_seen = i64::MAX;
    let bucket = if hours > 0 { "%Y-%m-%d %H" } else { "%Y-%m-%d" };
    for root in [home.join("sessions"), home.join("archived_sessions")] {
        for path in files(&root).unwrap_or_default() {
            let file = name(&path);
            if !file.starts_with("rollout-") || !file.ends_with(".jsonl") {
                continue;
            }
            let Ok(text) = fs::read_to_string(&path) else { continue };
            let has_records = text.contains("\"token_usage_record\"");
            let (mut account, mut model, mut previous_total) = (String::new(), String::new(), u64::MAX);
            for line in text.lines() {
                if !(line.contains("\"session_meta\"") || line.contains("\"turn_context\"") || line.contains("\"token_usage_record\"") || line.contains("\"token_count\"")) {
                    continue;
                }
                let Ok(entry) = serde_json::from_str::<Value>(line) else { continue };
                let payload = &entry["payload"];
                match s(&entry, "type") {
                    "session_meta" => account = s(payload, "creator_account_id").to_owned(),
                    "turn_context" => model = s(payload, "model").to_owned(),
                    _ => {}
                }
                let Some(time) = DateTime::parse_from_rfc3339(s(&entry, "timestamp")).ok().map(|t| t.timestamp_millis()) else { continue };
                let key = if account.is_empty() { UNKNOWN.to_owned() } else { account.clone() };
                if s(payload, "type") == "token_count" && payload["rate_limits"].is_object() {
                    let slot = quota.entry(key.clone()).or_insert((i64::MIN, Value::Null));
                    if time > slot.0 {
                        *slot = (time, payload["rate_limits"].clone());
                    }
                }
                let usage = if s(&entry, "type") == "token_usage_record" {
                    if !seen.insert(format!("{}|{}", s(payload, "response_id"), s(payload, "turn_id"))) {
                        continue;
                    }
                    payload["usage"].clone()
                } else if !has_records && s(payload, "type") == "token_count" && payload["info"].is_object() {
                    // Snapshots repeat until the next response; count one only when the running total moves.
                    let running = payload["info"]["total_token_usage"]["total_tokens"].as_u64().unwrap_or(0);
                    if running == previous_total {
                        continue;
                    }
                    previous_total = running;
                    payload["info"]["last_token_usage"].clone()
                } else {
                    continue;
                };
                if time < since || !usage.is_object() {
                    continue;
                }
                let n = |k: &str| usage[k].as_u64().unwrap_or(0);
                let (cached, written) = (n("cached_input_tokens"), n("cache_write_input_tokens"));
                // OpenAI's input_tokens already include cached tokens; split them like Claude's fields.
                let claude_like = json!({"input_tokens":n("input_tokens").saturating_sub(cached + written),"output_tokens":n("output_tokens"),
                    "cache_read_input_tokens":cached,"cache_creation_input_tokens":written});
                first_seen = first_seen.min(time);
                let cost = price_for(pricing, &model).map(|p| openai_cost(&usage, p));
                total.add_cost(&claude_like, cost);
                by_account.entry(key).or_default().add_cost(&claude_like, cost);
                by_model.entry(if model.is_empty() { "未知模型".to_owned() } else { model.clone() }).or_default().add_cost(&claude_like, cost);
                let day = beijing().timestamp_millis_opt(time).single().map(|d| d.format(bucket).to_string()).unwrap_or_default();
                by_day.entry(day).or_default().add_cost(&claude_like, cost);
            }
        }
    }
    Ok((total, by_account, by_model, by_day, quota, first_seen))
}

fn codex_quota_text(time: i64, limits: &Value) -> String {
    let when = |ms: i64| beijing().timestamp_millis_opt(ms).single().map(|d| d.format("%m-%d %H:%M").to_string()).unwrap_or_default();
    let mut parts = vec![];
    for key in ["primary", "secondary"] {
        let w = &limits[key];
        if let Some(used) = w["used_percent"].as_f64() {
            let label = match w["window_minutes"].as_i64().unwrap_or(0) { 300 => "5 小时".to_owned(), 10080 => "每周".to_owned(), m => format!("{} 小时", m / 60) };
            let reset = w["resets_at"].as_i64().map(|t| format!(" · 重置 {}", when(t * 1000))).unwrap_or_default();
            parts.push(format!("{label} 已用 {used:.0}%{reset}"));
        }
    }
    if parts.is_empty() { String::new() } else { format!("{}（记录于 {}）", parts.join("；"), when(time)) }
}

pub(super) fn usage_stats(ctx: &Context, r: &Value) -> Result<Value> {
    let days = r["Days"].as_i64().unwrap_or(7).clamp(0, 3650);
    // "Hours" selects a rolling window (e.g. the last 24 hours) with an hourly trend instead of days.
    let hours = r["Hours"].as_i64().unwrap_or(0).clamp(0, 24 * 31);
    let now = local_now();
    let today = now.date_naive();
    let since = if hours > 0 {
        now.timestamp_millis() - hours * 3_600_000
    } else if days == 0 {
        i64::MIN
    } else {
        (today - chrono::Duration::days(days - 1))
            .and_hms_opt(0, 0, 0)
            .unwrap()
            .and_local_timezone(beijing())
            .single()
            .unwrap()
            .timestamp_millis()
    };
    let (pricing, price_source) = if s(r, "App") == "codex" {
        load_openai_prices(ctx, r["RefreshPrices"] == true)
    } else {
        load_prices(ctx, r["RefreshPrices"] == true)
    };
    let priced = pricing["Models"].as_object().is_some_and(|m| !m.is_empty());
    let events = timeline(ctx);
    let owner = owners(ctx);
    let mut names: HashMap<String, String> = HashMap::new();
    for account in ctx.state()?["Accounts"].as_array().cloned().unwrap_or_default() {
        if s(&account, "RootKey") == "official" {
            names.insert(s(&account, "Uuid").to_owned(), s(&account, "Name").to_owned());
        }
    }
    let codex = s(r, "App") == "codex";
    if codex {
        // Emails cached by the adapter's Codex catalog; raw account IDs are the fallback.
        if let Some(map) = opt_json(&ctx.data.join("codex-accounts.json")).as_object() {
            for (id, email) in map {
                names.insert(id.clone(), email.as_str().unwrap_or_default().to_owned());
            }
        }
    }
    let label = |key: &str| -> String {
        match key {
            CLI => "Claude Code 命令行".into(),
            UNKNOWN | "" => "未能确定账号".into(),
            k if k.starts_with("3p:") => {
                let provider = &k[3..];
                if provider.is_empty() { "第三方提供商".into() } else { format!("第三方 · {provider}") }
            }
            k => names.get(k).cloned().unwrap_or_else(|| format!("账号 {}", short(k))),
        }
    };
    let (mut total, mut by_account, mut by_model, mut by_day) =
        (Tally::default(), BTreeMap::<String, Tally>::new(), BTreeMap::<String, Tally>::new(), BTreeMap::<String, Tally>::new());
    let mut quota: HashMap<String, (i64, Value)> = HashMap::new();
    let mut seen = HashSet::new();
    let mut first_seen = i64::MAX;
    let claude_files = if codex { Default::default() } else { ctx.transcripts()? };
    for (cli, path) in claude_files {
        let reader = BufReader::new(File::open(&path).map_err(err)?);
        for line in reader.lines() {
            let Ok(line) = line else { continue };
            let has_quota = line.contains("\"quotaLimits\"");
            if !has_quota && !line.contains("\"usage\"") {
                continue; // Cheap filter: most lines are prompts, tool output or metadata.
            }
            let Ok(entry) = serde_json::from_str::<Value>(&line) else { continue };
            let Some(time) = DateTime::parse_from_rfc3339(s(&entry, "timestamp")).ok().map(|t| t.timestamp_millis()) else { continue };
            let key = if s(&entry, "entrypoint") != "" && s(&entry, "entrypoint") != "claude-desktop" {
                CLI.to_owned()
            } else {
                // The latest recorded switch at or before this request decides the account.
                let at = events.partition_point(|(t, _)| *t <= time);
                match at.checked_sub(1).map(|i| events[i].1.clone()) {
                    Some(k) if !k.is_empty() => k,
                    _ => owner.get(&cli).cloned().unwrap_or_else(|| UNKNOWN.to_owned()),
                }
            };
            if has_quota && entry["quotaLimits"].is_object() {
                let slot = quota.entry(key.clone()).or_insert((i64::MIN, Value::Null));
                if time > slot.0 {
                    *slot = (time, entry["quotaLimits"].clone());
                }
            }
            let message = &entry["message"];
            if s(&entry, "type") != "assistant" || !message["usage"].is_object() {
                continue;
            }
            let model = s(message, "model");
            if model.is_empty() || model.starts_with('<') {
                continue; // "<synthetic>" placeholders carry no real request.
            }
            let id = format!("{}|{}", s(message, "id"), s(&entry, "requestId"));
            if id == "|" || !seen.insert(id) || time < since {
                continue;
            }
            first_seen = first_seen.min(time);
            let price = price_for(&pricing, model);
            total.add(&message["usage"], price);
            by_account.entry(key).or_default().add(&message["usage"], price);
            by_model.entry(model.to_owned()).or_default().add(&message["usage"], price);
            let bucket = if hours > 0 { "%Y-%m-%d %H" } else { "%Y-%m-%d" };
            let day = beijing().timestamp_millis_opt(time).single()
                .map(|d| d.format(bucket).to_string()).unwrap_or_default();
            by_day.entry(day).or_default().add(&message["usage"], price);
        }
    }
    if codex {
        (total, by_account, by_model, by_day, quota, first_seen) = codex_usage(ctx, since, hours, &pricing)?;
    }
    let all = total.total();
    let mut accounts: Vec<Value> = by_account.iter().map(|(key, tally)| {
        let mut row = tally.json(all, priced);
        row["Key"] = json!(key);
        row["Name"] = json!(label(key));
        row["QuotaText"] = json!(quota.get(key).map(|(time, q)| if codex { codex_quota_text(*time, q) } else { quota_text(*time, q) }).unwrap_or_default());
        row
    }).collect();
    accounts.sort_by_key(|row| std::cmp::Reverse(row["Total"].as_u64().unwrap_or(0)));
    let mut models: Vec<Value> = by_model.iter().map(|(model, tally)| {
        let mut row = tally.json(all, priced);
        row["Name"] = json!(model);
        row
    }).collect();
    models.sort_by_key(|row| std::cmp::Reverse(row["Total"].as_u64().unwrap_or(0)));
    // A continuous run of days, so empty days show as gaps instead of disappearing.
    let span = if hours > 0 { hours } else if days == 0 { 30 } else { days.min(30) };
    let peak = by_day.values().map(Tally::total).max().unwrap_or(0).max(1);
    let trend: Vec<Value> = (0..span).map(|back| { // newest first
        let (date, label) = if hours > 0 {
            let at = now - chrono::Duration::hours(back);
            (at.format("%Y-%m-%d %H").to_string(), at.format("%H:00").to_string())
        } else {
            let date = (today - chrono::Duration::days(back)).format("%Y-%m-%d").to_string();
            let label = date[5..].to_owned();
            (date, label)
        };
        let tally = by_day.get(&date).cloned().unwrap_or_default();
        json!({"Date":date,"Label":label,"Total":tally.total(),"TotalText":tokens(tally.total()),"Requests":tally.requests,
            "Width":(tally.total() as f64 / peak as f64 * 420.0).max(if tally.total()>0{2.0}else{0.0})})
    }).collect();
    let mut out = json!({"Days":days,"Hours":hours,"TrendTitle":if hours>0{"每小时趋势"}else{"每日趋势"},"Totals":total.json(all,priced),"Accounts":accounts,"Models":models,"Trend":trend,"Priced":priced,"App":if codex{"codex"}else{"claude"},"PriceSource":price_source,
        "TimelineEvents":events.len(),"Since":if first_seen==i64::MAX{Value::Null}else{json!(first_seen)},
        "Note":"按本工具记录的切换时间推算每个请求所属账号；在 Claude 内自行换号的时段无法区分。只统计本机对话文件里的记录，不是官方账单。"});
    if codex {
        out["Note"] = json!("Codex 会话自带创建它的账号，按账号统计是准确的。只统计本机会话文件里的记录，不是官方账单；OpenAI 的 input_tokens 已含缓存读取，这里拆开显示。费用按 OpenAI 官方 Standard 价格折算（超过 272K 的提示按长上下文价格），没有公开价格的模型（如 codex-auto-review）不计入，金额后带“+”。");
    }
    Ok(out)
}

fn quota_text(time: i64, q: &Value) -> String {
    let when = |ms: i64| beijing().timestamp_millis_opt(ms).single().map(|d| d.format("%m-%d %H:%M").to_string()).unwrap_or_default();
    let mut text = format!("{} · {}", s(q, "rateLimitType"), s(q, "status"));
    if let Some(reset) = q["resetsAt"].as_i64() {
        text += &format!(" · 重置 {}", when(reset * 1000));
    }
    format!("{text}（记录于 {}）", when(time))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn prices_follow_the_official_rules() {
        let opus = json!({"Input":4.0,"CacheWrite5m":5.0,"CacheWrite1h":8.0,"CacheRead":0.2,"Output":20.0,"Fast":{"Input":8.0,"Output":40.0}});
        let close = |a: f64, b: f64| assert!((a - b).abs() < 1e-9, "{a} != {b}");
        // 1M each of input, output, read; 1M writes of which 0.25M are 1-hour.
        let usage = json!({"cache_creation":{"ephemeral_1h_input_tokens":250_000}});
        close(request_cost(&usage, &opus, 1_000_000, 1_000_000, 1_000_000, 1_000_000), 4.0 + 20.0 + 0.2 + 0.75 * 5.0 + 0.25 * 8.0);
        let fast = json!({"speed":"fast","inference_geo":"us"});
        close(request_cost(&fast, &opus, 1_000_000, 1_000_000, 1_000_000, 0), (8.0 + 40.0 + 0.4) * 1.1);
        let haiku = json!({"Input":0.1,"Output":0.5,"CacheRead":0.01,"CacheWrite5m":0.125,"CacheWrite1h":0.2,
            "LongPrompt":{"Threshold":100000,"Input":0.5,"Output":2.5,"CacheRead":0.05,"CacheWrite5m":0.625,"CacheWrite1h":1.0}});
        close(request_cost(&json!({}), &haiku, 100_000, 0, 0, 0), 0.01);
        close(request_cost(&json!({}), &haiku, 100_001, 0, 0, 0), 100_001.0 * 0.5 / 1e6);
        let prices = json!({"Models":{"claude-opus-5":{"Input":5.0},"claude-opus-5-5":{"Input":4.0}}});
        assert_eq!(price_for(&prices, "claude-opus-5-5").unwrap()["Input"], 4.0);
        assert_eq!(price_for(&prices, "claude-opus-5-20261001").unwrap()["Input"], 5.0);
        assert!(price_for(&prices, "claude-opus-55").is_none());
    }
    #[test]
    fn codex_usage_counts_responses_once_per_creator_account() {
        let root = env::temp_dir().join(format!("claude-studio-codex-usage-{}-{}", std::process::id(), now_nanos()));
        let ctx = Context::new(PathBuf::new(), PathBuf::new(), Some(root.clone())).unwrap();
        write_json(&ctx.app.join("config.json"), &json!({})).unwrap();
        write_json(&ctx.data.join("codex-accounts.json"), &json!({"acct-a":"a@example.test"})).unwrap();
        write_json(&ctx.data.join("pricing-openai.json"), &json!({"FetchedAt":"test","Models":{"gpt-test":{"Input":1e6,"CacheRead":1e5,"Output":2e6}}})).unwrap();
        let usage = |input: u64, cached: u64, out: u64| json!({"input_tokens":input,"cached_input_tokens":cached,"cache_write_input_tokens":0,"output_tokens":out});
        let record = |id: &str, t: &str, u: Value| json!({"timestamp":t,"type":"token_usage_record","payload":{"response_id":id,"turn_id":"t","usage":u}}).to_string();
        let lines = [
            json!({"timestamp":"2026-10-28T01:00:00Z","type":"session_meta","payload":{"creator_account_id":"acct-a"}}).to_string(),
            json!({"timestamp":"2026-10-28T01:00:01Z","type":"turn_context","payload":{"model":"gpt-test"}}).to_string(),
            record("r1", "2026-10-28T01:00:02Z", usage(100, 60, 7)),
            record("r1", "2026-10-28T01:00:02Z", usage(100, 60, 7)), // the same response logged twice
            record("r2", "2026-10-28T01:00:03Z", usage(50, 0, 3)),
            json!({"timestamp":"2026-10-28T01:00:04Z","type":"event_msg","payload":{"type":"token_count",
                "rate_limits":{"primary":{"used_percent":32.0,"window_minutes":10080,"resets_at":1793774914}}}}).to_string(),
        ];
        let folder = root.join("codex/sessions/2026/10/28");
        fs::create_dir_all(&folder).unwrap();
        fs::write(folder.join("rollout-a.jsonl"), lines.join("\n")).unwrap();
        let stats = usage_stats(&ctx, &json!({"App":"codex","Days":0})).unwrap();
        assert_eq!(stats["Totals"]["Requests"], 2);
        assert_eq!(stats["Totals"]["Input"], 90, "cached tokens are split out of OpenAI's input_tokens");
        assert_eq!(stats["Totals"]["CacheRead"], 60);
        assert_eq!(stats["Totals"]["CostText"], "$116.00", "(40 uncached + 6 cached + 14 output) + (50 + 6)");
        assert_eq!(stats["Accounts"][0]["Name"], "a@example.test");
        assert!(s(&stats["Accounts"][0], "QuotaText").starts_with("每周 已用 32%"));
        assert_eq!(stats["Models"][0]["Name"], "gpt-test");
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn counts_each_request_once_and_attributes_by_switch_time() {
        let root = env::temp_dir().join(format!("claude-studio-usage-{}-{}", std::process::id(), now_nanos()));
        let ctx = Context::new(PathBuf::new(), PathBuf::new(), Some(root.clone())).unwrap();
        let (a, b, org) = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222", "33333333-3333-4333-8333-333333333333");
        write_json(&ctx.app.join("config.json"), &json!({"lastKnownAccountUuid":b})).unwrap();
        let cli = "44444444-4444-4444-8444-444444444444";
        write_json(&ctx.registry().join(a).join(org).join(format!("local_{cli}.json")),
            &json!({"sessionId":format!("local_{cli}"),"cliSessionId":cli,"createdAt":1})).unwrap();
        // Switch to B at 2026-10-28 12:00 Beijing time.
        let switch = ctx.data.join("private-auth-backups/20261028-120000-000001-switch-account");
        write_json(&switch.join("transaction.json"), &json!({"Status":"committed","Account":b})).unwrap();
        let line = |id: &str, t: &str, out: u64| json!({"type":"assistant","entrypoint":"claude-desktop","timestamp":t,"requestId":id,
            "message":{"id":id,"model":"claude-test","usage":{"input_tokens":10,"output_tokens":out,"cache_read_input_tokens":30,"cache_creation_input_tokens":0}}}).to_string();
        let body = [
            line("r1", "2026-10-28T03:00:00Z", 5), // 11:00 Beijing → owner A
            line("r1", "2026-10-28T03:00:00Z", 5), // second content block of the same request
            line("r2", "2026-10-28T05:00:00Z", 7), // 13:00 Beijing → B after the switch
            json!({"type":"system","timestamp":"2026-10-28T05:00:01Z","quotaLimits":{"status":"allowed_warning","rateLimitType":"five_hour","resetsAt":1793000000}}).to_string(),
        ].join("\n");
        fs::create_dir_all(ctx.claude.join("projects/p")).unwrap();
        fs::write(ctx.claude.join("projects/p").join(format!("{cli}.jsonl")), body).unwrap();
        write_json(&ctx.data.join("pricing.json"), &json!({"Models":{"claude-test":{"Input":1000000.0,"Output":0.0,"CacheRead":0.0,"CacheWrite":0.0}}})).unwrap();
        let rolling = usage_stats(&ctx, &json!({"Hours":24})).unwrap();
        assert_eq!(rolling["Trend"].as_array().unwrap().len(), 24);
        assert_eq!(rolling["TrendTitle"], "每小时趋势");        let stats = usage_stats(&ctx, &json!({"Days":0})).unwrap();
        assert_eq!(stats["Totals"]["Requests"], 2);
        assert_eq!(stats["Totals"]["Output"], 12);
        assert_eq!(stats["Totals"]["CostText"], "$20.00");
        let rows = stats["Accounts"].as_array().unwrap();
        let of = |k: &str| rows.iter().find(|r| s(r, "Key") == k).unwrap().clone();
        assert_eq!(of(a)["Output"], 5);
        assert_eq!(of(b)["Output"], 7);
        assert!(s(&of(b), "QuotaText").starts_with("five_hour · allowed_warning"));
        assert_eq!(stats["Models"][0]["Name"], "claude-test");
        fs::remove_dir_all(root).unwrap();
    }
}
