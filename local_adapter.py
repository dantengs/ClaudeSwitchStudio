"""Local identity/provider adapter.

Catalog operations are read-only. Mutations require an explicit maintenance confirmation,
closed Claude Desktop processes, and a timestamped, verified preimage backup. Desktop is only
closed when the request carries QuitClaude, which the UI sends after the user confirms the
exit dialog; independent Claude Code CLI sessions are never closed.
"""
from __future__ import annotations
import argparse
import base64
from contextlib import closing, contextmanager
import hashlib
import hmac
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone, timedelta

PROFILE_ID = '5990894a-e2e0-4fcb-a381-e66591617623'
AUTH_ITEMS = ('Local State', 'Network', 'Local Storage', 'Session Storage', 'config.json')
GATEWAY_KEYS = ('inferenceProvider', 'inferenceGatewayBaseUrl', 'inferenceGatewayApiKey',
                'inferenceGatewayAuthScheme', 'inferenceModels', 'modelDiscoveryEnabled')
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# Account-specific values removed from config.json when preparing a blank login for a new account.
SIGNED_IN_KEYS = ('lastKnownAccountUuid', 'bootSideSessions')
DESKTOP_MARKERS = ('\\windowsapps\\claude_', '\\anthropicclaude\\')
APP_ID = re.compile(r'[A-Za-z0-9.\-]+_[a-z0-9]{13}![A-Za-z0-9.\-]+')


def read(path, default=None):
    path = Path(path)
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding='utf-8-sig'))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(5) + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def archive_file(source,destination):
    """Archive an owned file across volumes, verifying the copy before unlinking it."""
    source,destination=Path(source),Path(destination)
    if not source.is_file() or source.is_symlink() or destination.exists():
        raise ValueError('归档目标不安全或已存在，已停止。')
    destination.parent.mkdir(parents=True,exist_ok=True)
    expected=sha(source)
    shutil.copy2(source,destination)
    if sha(destination)!=expected or sha(source)!=expected:
        raise ValueError('归档校验失败，原文件已保留。')
    source.unlink()


def replace_within(source,destination,allowed_root):
    source,destination,allowed_root=Path(source),Path(destination),Path(allowed_root).resolve()
    if not source.resolve().is_relative_to(allowed_root) or not destination.resolve().is_relative_to(allowed_root):
        raise ValueError('本地替换路径超出已核对的数据目录。')
    os.replace(source,destination)


def stamp():
    return datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S-%f')


def checked_name(name):
    if not name or len(name) > 64 or name != name.strip() or not all(c.isalnum() or c in ' _-' for c in name):
        raise ValueError('登录快照内部标识无效，请刷新后重新选择账号。')
    if name.upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        raise ValueError('该名称为系统保留名称。')
    return name


def app_paths(fixture=None):
    if fixture:
        root = Path(fixture).resolve()
        return root / 'desktop', root / 'desktop-3p', root / 'profiles', root / 'user'
    user = Path(os.environ['USERPROFILE'])
    local = user / 'AppData/Local'
    candidates = [local / 'Claude-Data/claude_pzs8sxrjxfjjc', local / 'Claude',
                  user / 'AppData/Roaming/Claude', local / 'Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude']
    official = next((p for p in candidates if (p / 'config.json').exists()), None)
    if official is None:
        raise ValueError('找不到官方桌面版的数据目录。')
    return official, local / 'Claude-3p', user / '.claude-profiles', user


def provider_rows(db_path=None):
    db = Path(db_path or Path(os.environ['USERPROFILE']) / '.cc-switch/cc-switch.db')
    if not db.is_file():
        return []
    # URI read-only mode includes an active WAL snapshot; never open the provider database writable.
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)) as connection:
        rows = connection.execute("select id,name,settings_config,meta from providers where app_type='claude-desktop'").fetchall()
    result = []
    for identifier, name, config, meta in rows:
        if identifier == 'claude-desktop-official':
            continue
        result.append({'id': identifier, 'name': name, 'config': json.loads(config), 'meta': json.loads(meta or '{}')})
    return result


def public_provider(row):
    env = row['config'].get('env', {})
    url = urllib.parse.urlsplit(env.get('ANTHROPIC_BASE_URL', ''))
    endpoint = urllib.parse.urlunsplit((url.scheme, url.hostname + (f':{url.port}' if url.port else '') if url.hostname else '', url.path, '', ''))
    mode = row['meta'].get('claudeDesktopMode', 'direct')
    compatible = row['meta'].get('apiFormat', 'anthropic') == 'anthropic'
    configured = bool(env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY')) and bool(url.hostname)
    return {'Id': row['id'], 'Name': row['name'], 'Kind': '第三方提供商', 'Endpoint': endpoint,
            'Mode': mode, 'ModeLabel': '本地模型映射' if mode == 'proxy' else '直连',
            'CanSwitch': configured and compatible, 'IsCurrent': False,
            'Status': '已配置' if configured and compatible else '配置待补全',
            'Email': '', 'EmailStatus': 'API 提供商不一定有邮箱'}


def catalog(db_path=None, fixture=None, data_root=None):
    official, third, _, _ = app_paths(fixture)
    rows = provider_rows(db_path)
    mode = read(official / 'claude_desktop_config.json').get('deploymentMode', '1p')
    meta = read(third / 'configLibrary/_meta.json')
    profile = read(third / 'configLibrary' / (str(meta.get('appliedId', '')) + '.json'))
    actual = profile.get('inferenceGatewayBaseUrl', '')
    selected = read(Path(data_root) / 'selected-provider.json') if data_root else {}
    public = []
    for row in rows:
        item = public_provider(row)
        item['IsCurrent'] = mode == '3p' and (
            actual == item['Endpoint'] or (selected.get('Id') == item['Id'] and actual == selected.get('Gateway')))
        if item['IsCurrent']:
            item['Status'] = '当前提供商'
        public.append(item)
    return {'Providers': public, 'OfficialActive': mode != '3p', 'Warning': ''}


def powershell(script):
    # Scripts avoid inner double quotes: Windows PowerShell 5.1 native argument passing differs from 7.
    output = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                             '[Console]::OutputEncoding=[Text.Encoding]::UTF8;' + script],
                            creationflags=NO_WINDOW, capture_output=True, text=True, encoding='utf-8')
    if output.returncode:
        raise ValueError('无法读取进程信息，已停止，未修改任何文件。')
    return output.stdout.strip()


# Per desktop app: process names, install-path markers, Start-menu app ID and how its CLI behaves.
# Claude Code CLI never touches Desktop login files, so it does not block. A separate Codex CLI
# shares ~/.codex/auth.json and may rewrite it while refreshing a token, so it blocks a switch.
APPS = {
    'claude': {'Label': 'Claude Desktop', 'Names': ('claude.exe',), 'Markers': DESKTOP_MARKERS,
               'Package': 'claude_', 'AppIdPrefix': 'Claude_', 'DefaultApp': 'Claude', 'Exe': 'claude.exe',
               'Fixture': 'processes.json', 'CliBlocks': False, 'CliLabel': 'Claude Code 命令行'},
    'codex': {'Label': 'Codex', 'Names': ('chatgpt.exe', 'codex.exe'), 'Markers': ('\\windowsapps\\openai.codex_',),
              'Package': 'openai.codex_', 'AppIdPrefix': 'OpenAI.Codex_', 'DefaultApp': 'App', 'Exe': 'chatgpt.exe',
              'Fixture': 'codex-processes.json', 'CliBlocks': True, 'CliLabel': 'Codex 命令行'},
    # A per-user install, not MSIX: reopened from the executable it was running from.
    'agytools': {'Label': 'Antigravity Tools', 'Names': ('antigravity-tools.exe',), 'Markers': ('\\antigravity tools\\',),
                 'Package': 'antigravity-tools_', 'AppIdPrefix': 'com.lbjlaq.antigravity', 'DefaultApp': 'App', 'Exe': 'antigravity-tools.exe',
                 'Fixture': 'agy-processes.json', 'CliBlocks': True, 'CliLabel': 'Antigravity Tools'},
}


def claude_processes(fixture=None, app='claude'):
    """Processes of one app in this Windows session, classified by executable path and parent.

    desktop: the desktop app (MSIX or per-user install) and its Electron helpers.
    agent:   a CLI started by the desktop app; it exits together with it.
    cli:     an independent CLI session started elsewhere.
    unknown: no readable executable path; never closed automatically.
    """
    spec = APPS[app]
    if fixture:
        rows = read(Path(fixture) / spec['Fixture'], [])
    else:
        names = ','.join("'" + n + "'" for n in spec['Names'])
        text = powershell("$sid=[Diagnostics.Process]::GetCurrentProcess().SessionId;$names=@(" + names + ");"
                          "@(Get-CimInstance Win32_Process | Where-Object { $names -contains $_.Name.ToLower() -and $_.SessionId -eq $sid } | "
                          "ForEach-Object { [pscustomobject]@{Pid=[int]$_.ProcessId;ParentPid=[int]$_.ParentProcessId;Path=[string]$_.ExecutablePath} }) | ConvertTo-Json -Compress")
        rows = json.loads(text) if text else []
        rows = [rows] if isinstance(rows, dict) else rows
    rows = [{'Pid': int(r['Pid']), 'ParentPid': int(r.get('ParentPid') or 0), 'Path': r.get('Path') or ''} for r in rows]
    desktop = {r['Pid'] for r in rows if any(m in r['Path'].lower() for m in spec['Markers'])}
    parents = {r['Pid']: r['ParentPid'] for r in rows}
    for row in rows:
        ancestor, seen = row['ParentPid'], set()
        while ancestor in parents and ancestor not in desktop and ancestor not in seen:
            seen.add(ancestor); ancestor = parents[ancestor]
        if row['Pid'] in desktop:
            row['Kind'] = 'desktop'
        elif ancestor in desktop:
            row['Kind'] = 'agent'
        else:
            row['Kind'] = 'cli' if row['Path'] else 'unknown'
    return rows


def is_blocking(row, app):
    return row['Kind'] != 'cli' or APPS[app]['CliBlocks']


def blocking_processes(fixture=None, app='claude'):
    return [r for r in claude_processes(fixture, app) if is_blocking(r, app)]


def process_summary(fixture=None, app='claude'):
    rows = claude_processes(fixture, app)
    count = lambda kind: sum(r['Kind'] == kind for r in rows)
    cli_blocks = APPS[app]['CliBlocks'] and count('cli') > 0
    return {'Blocking': any(is_blocking(r, app) for r in rows), 'DesktopRunning': count('desktop') > 0,
            'Desktop': count('desktop'), 'Agents': count('agent'), 'Cli': count('cli'), 'Unknown': count('unknown'),
            'CanQuit': count('unknown') == 0 and not cli_blocks, 'App': app, 'Label': APPS[app]['Label'],
            'CliBlocks': cli_blocks, 'CliLabel': APPS[app]['CliLabel']}


def ensure_closed(fixture=None, app='claude'):
    blocking = blocking_processes(fixture, app)
    if not blocking:
        return
    if app == 'claude':
        raise ValueError(f'Claude Desktop 仍在运行（{len(blocking)} 个相关进程）。请退出后重试，或在确认窗口中选择自动退出并重新打开。'
                         '独立运行的 Claude Code 命令行不受影响。')
    if all(r['Kind'] == 'cli' for r in blocking):
        raise ValueError('有独立运行的 Codex 命令行，它可能在刷新登录时覆盖账号文件。请先结束这些命令行会话后重试。')
    raise ValueError(f'Codex 仍在运行（{len(blocking)} 个相关进程）。请退出后重试，或在确认窗口中选择自动退出并重新打开。')


def launch_target(rows=(), fixture=None, app='claude'):
    """Remember how to reopen the desktop app: its package app ID, or the executable of a per-user install."""
    spec = APPS[app]
    for row in rows:
        if row['Kind'] != 'desktop' or not row['Path']:
            continue
        path = Path(row['Path'])
        package = next((part for part in path.parts if part.lower().startswith(spec['Package']) and '__' in part), '')
        if package:
            family = package.split('_')[0] + '_' + package.rsplit('__', 1)[1]
            return {'Kind': 'appx', 'Id': installed_app_id(family, fixture) or family + '!' + spec['DefaultApp']}
        return {'Kind': 'exe', 'Path': str(path)}
    if fixture:
        return read(Path(fixture) / ('launch-target.json' if app == 'claude' else app + '-launch-target.json'), {}) or None
    app_id = installed_app_id(spec['AppIdPrefix'], fixture)
    if app_id:
        return {'Kind': 'appx', 'Id': app_id}
    if app == 'agytools':
        for folder in (os.environ.get('ProgramFiles', ''), 'D:\\Program Files', os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs')):
            candidate = Path(folder) / 'Antigravity Tools' / 'antigravity-tools.exe'
            if folder and candidate.is_file():
                return {'Kind': 'exe', 'Path': str(candidate)}
        return None
    squirrel = Path(os.environ.get('LOCALAPPDATA', '')) / 'AnthropicClaude/claude.exe'
    return {'Kind': 'exe', 'Path': str(squirrel)} if app == 'claude' and squirrel.is_file() else None


def installed_app_id(prefix, fixture=None):
    if fixture:
        return ''
    found = powershell("Get-StartApps | Where-Object { $_.AppID -like '" + prefix.replace("'", '') + "*!*' } | "
                       "Select-Object -First 1 -ExpandProperty AppID")
    return found if APP_ID.fullmatch(found) else ''


def record_fixture(fixture, entry):
    log = Path(fixture) / 'process-log.json'
    atomic_json(log, read(log, []) + [entry])


def quit_desktop(fixture=None, timeout=25, app='claude'):
    """Close the desktop app and its own agents after the user confirmed; returns how to reopen it."""
    spec = APPS[app]
    rows = claude_processes(fixture, app)
    if any(r['Kind'] == 'unknown' for r in rows):
        raise ValueError(f'发现无法识别来源的 {spec["Label"]} 进程，未自动关闭。请手动退出后重试。')
    if spec['CliBlocks'] and any(r['Kind'] == 'cli' for r in rows):
        raise ValueError(f'有独立运行的 {spec["CliLabel"]}，工具不会关闭它。请先结束这些会话后重试。')
    targets = [r for r in rows if r['Kind'] in ('desktop', 'agent')]
    if not targets:
        return None
    target = launch_target(rows, fixture, app)
    if fixture:
        record_fixture(fixture, {'Quit': sorted(r['Pid'] for r in targets)} if app == 'claude' else {'Quit': sorted(r['Pid'] for r in targets), 'App': app})
        atomic_json(Path(fixture) / spec['Fixture'], [r for r in rows if r['Kind'] == 'cli'])
        return target
    def close(force):
        # Re-read before each round so a reused PID is never closed by mistake.
        current = [r for r in claude_processes(None, app) if r['Kind'] in ('desktop', 'agent')]
        pids = {r['Pid'] for r in current}
        for row in current:
            if row['ParentPid'] not in pids or force:
                subprocess.run(['taskkill.exe', '/PID', str(row['Pid']), '/T'] + (['/F'] if force else []),
                               creationflags=NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def wait(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not blocking_processes(None, app):
                return True
            time.sleep(.4)
        return not blocking_processes(None, app)
    # Electron apps normally hide to the tray on WM_CLOSE, so a forced exit follows a short grace period.
    close(False)
    if not wait(4):
        close(True)
        if not wait(timeout):
            raise ValueError(f'{spec["Label"]} 未能在 {timeout} 秒内退出，未修改登录文件。请手动退出后重试。')
    time.sleep(1.5)  # Let Chromium release LevelDB and SQLite handles before files are copied.
    return target


def launch_desktop(target, fixture=None, app='claude'):
    if not target:
        return False
    if fixture:
        record_fixture(fixture, {'Launch': target})
        return True
    if target['Kind'] == 'appx':
        if not APP_ID.fullmatch(target['Id']):
            return False
        command = ['explorer.exe', 'shell:AppsFolder\\' + target['Id']]
    else:
        path = Path(target['Path'])
        if not path.is_file() or path.name.lower() != APPS[app]['Exe']:
            return False
        command = ['explorer.exe', str(path)]
    # Explorer becomes the parent, so the app outlives this adapter and the Studio window.
    subprocess.Popen(command, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    return True


@contextmanager
def auth_operation(profiles):
    """All new installations serialize authentication changes through one per-user lock."""
    profiles = Path(profiles)
    profiles.mkdir(parents=True, exist_ok=True)
    with (profiles / '.studio-auth.lock').open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('另一个版本或窗口正在处理登录，请稍后重试。') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def transaction_state(backup, profiles, value):
    # Shared records let a different installation detect a previous interrupted operation.
    record = dict(value, Backup=str(backup))
    atomic_json(Path(profiles) / '.studio-transactions' / (Path(backup).name + '.json'), record)
    atomic_json(Path(backup) / 'transaction.json', record)


def reject_pending(data_root, profiles=None):
    root=Path(data_root)/'private-auth-backups'
    journals = list(root.glob('*/transaction.json')) if root.exists() else []
    if profiles:
        journals += list((Path(profiles) / '.studio-transactions').glob('*.json'))
    for journal in journals:
        status=read(journal).get('Status')
        if status not in ('committed','rolled-back'):
            raise ValueError('存在未完成的账号操作，请先核对对应时间戳备份，避免覆盖当前状态。')


def tree_files(path):
    path = Path(path)
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise ValueError('账号快照路径包含链接，请先检查。')
    if path.is_file():
        return [path]
    files = []
    for folder, directories, names in os.walk(path, followlinks=False):
        for entry in [Path(folder) / n for n in directories + names]:
            if entry.is_symlink() or (hasattr(entry, 'is_junction') and entry.is_junction()):
                raise ValueError('账号快照内存在链接，已停止。')
        files.extend(Path(folder) / n for n in names)
    return files


def snapshot(paths, data_root, purpose):
    backup = Path(data_root) / 'private-auth-backups' / (stamp() + '-' + purpose)
    backup.mkdir(parents=True, exist_ok=False)
    # These snapshots contain authentication state. Restrict the newly created private folder on Windows.
    if os.name == 'nt':
        identity = subprocess.check_output(['whoami.exe'], creationflags=NO_WINDOW, text=True).strip()
        protected = subprocess.run(['icacls.exe', str(backup), '/inheritance:r', '/grant:r', identity + ':(OI)(CI)F'],
                                   creationflags=NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if protected.returncode:
            raise ValueError('无法限制账号备份访问权限，已停止操作。')
    records = []
    for index, source in enumerate(map(Path, paths)):
        if not source.exists():
            records.append({'Source': str(source), 'Exists': False})
            continue
        target = backup / f'{index:02d}'
        source_files = tree_files(source)
        before = {str(f): sha(f) for f in source_files}
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
        for f in source_files:
            destination = target / f.relative_to(source) if source.is_dir() else target
            if sha(f) != before[str(f)] or sha(destination) != before[str(f)]:
                raise ValueError('备份期间账号文件发生变化，已停止。')
        records.append({'Source': str(source), 'Exists': True, 'Backup': str(target), 'Files': before})
    atomic_json(backup / 'manifest.json', {'Time': stamp(), 'Purpose': purpose, 'Records': records, 'Verified': True})
    return backup


def patch_files(changes, data_root, fixture=None, ready_callback=None):
    changes = [(Path(p), value) for p, value in changes]
    ensure_closed(fixture)
    backup = snapshot([p for p, _ in changes], data_root, 'provider-switch')
    ensure_closed(fixture)
    profiles = app_paths(fixture)[2]
    records = read(backup / 'manifest.json')['Records']
    transaction_state(backup, profiles, {'Status': 'applying', 'Targets': [str(p) for p, _ in changes]})
    written = []
    try:
        for (path, value), record in zip(changes, records):
            if record['Exists'] and sha(path) != record['Files'][str(path)]:
                raise ValueError('配置已被其他程序修改，已停止。')
            if not record['Exists'] and path.exists():
                raise ValueError('目标配置在操作期间出现，已停止。')
            atomic_json(path, value)
            written.append(record)
        if ready_callback:
            ready_callback()
        transaction_state(backup, profiles, {'Status': 'committed', 'Targets': [str(p) for p, _ in changes]})
    except Exception:
        # Restore only files already written, preserving a timestamped copy of any partial outputs.
        for index, record in enumerate(reversed(written)):
            path = Path(record['Source'])
            if path.exists():
                displaced = backup / 'partial-output' / str(index)
                displaced.parent.mkdir(exist_ok=True)
                shutil.copy2(path, displaced)
            if record['Exists']:
                shutil.copy2(record['Backup'], path)
            elif path.exists():
                archive_file(path, backup / f'new-file-{index}')
        transaction_state(backup, profiles, {'Status': 'rolled-back'})
        raise
    return backup


def cached_login(config):
    # Presence only: encrypted values stay local and are never returned to the UI.
    return any(isinstance(config.get(key), (str, dict)) and bool(config[key])
               for key in ('oauth:tokenCache', 'oauth:tokenCacheV2'))


def account_profile(profiles, account):
    matches = [entry for entry in profiles.iterdir() if entry.is_dir() and not entry.name.startswith('.')
               and read(entry / 'config.json').get('lastKnownAccountUuid') == account] if profiles.exists() else []
    if len(matches) > 1:
        raise ValueError('同一账号存在多个登录快照，请先检查，工具不会自行覆盖。')
    return matches[0] if matches else profiles / ('desktop-' + str(uuid.UUID(account)))


def auth_fingerprint(root):
    result = {}
    for item in AUTH_ITEMS:
        path = root / item
        if not path.exists():
            raise ValueError(f'账号文件不完整：{item}。请自行登录桌面版后重试。')
        result[item] = {str(f.relative_to(path)) if path.is_dir() else '.': sha(f) for f in tree_files(path)}
    return result


def capture_target(request, official):
    """Validate a capture without touching files, so it can run before Desktop is closed."""
    if read(official / 'claude_desktop_config.json').get('deploymentMode', '1p') == '3p':
        raise ValueError('当前为第三方模式，请先切回官方并自行登录后，再保存官方账号。')
    config = read(official / 'config.json')
    current = config.get('lastKnownAccountUuid')
    if not current:
        raise ValueError('尚未检测到当前账号，请先自行登录。')
    uuid.UUID(current)
    expected = request.get('ExpectedAccount')
    if expected and expected != current:
        raise ValueError('桌面版当前账号与所选卡片不一致。请刷新；需要收录其他账号时，请先自行登录该账号。')
    if not cached_login(config):
        raise ValueError('只发现历史账号信息，未发现桌面版登录缓存。打开 Claude Code 不能代替桌面版登录，请自行在 Claude Desktop 中登录后收录。')
    display = str(request.get('DisplayName', request.get('Name', ''))).strip()
    if len(display) > 120 or any(ord(c) < 32 for c in display):
        raise ValueError('显示名称请使用 120 字以内的单行文字，可直接使用邮箱。')
    return current, config, display


def save_account(request, data_root, fixture=None):
    ensure_closed(fixture)
    official, _, profiles, user = app_paths(fixture)
    current, config, display = capture_target(request, official)
    final = account_profile(profiles, current)
    before = auth_fingerprint(official)
    backup = snapshot([official / item for item in AUTH_ITEMS] + [final], data_root, 'save-account')
    ensure_closed(fixture)
    staging = profiles / ('.studio-staging-' + secrets.token_hex(8))
    staging.mkdir(parents=True, exist_ok=False)
    if os.name == 'nt':
        identity = subprocess.check_output(['whoami.exe'], creationflags=NO_WINDOW, text=True).strip()
        result = subprocess.run(['icacls.exe', str(staging), '/inheritance:r', '/grant:r', identity + ':(OI)(CI)F'],
                                creationflags=NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode:
            raise ValueError('无法限制登录快照访问权限，已停止。')
    for item in AUTH_ITEMS:
        source = official / item
        if not source.exists():
            raise ValueError(f'账号文件不完整：{item}')
        if source.is_dir():
            shutil.copytree(source, staging / item)
        else:
            shutil.copy2(source, staging / item)
    oauth = read(user / '.claude.json').get('oauthAccount', {})
    email = oauth.get('emailAddress', '') if oauth.get('accountUuid') == current else ''
    confirmed=read(Path(data_root)/'identities.json').get('Accounts',{}).get(current,{})
    display_email=confirmed.get('Email','') if confirmed.get('ConfirmedBy')=='user' else email
    atomic_json(staging / 'studio-profile.json', {'Version': 3, 'Account': current, 'DisplayName': display,
                'Email': display_email, 'EmailSource': 'user-confirmed' if confirmed.get('ConfirmedBy')=='user' else 'local-oauth-metadata',
                'SavedAt': stamp(), 'Validation': 'local-cache-only'})
    if email:
        atomic_json(staging / 'claude.json', {'oauthAccount': oauth})
    if (final/'label.txt').is_file():shutil.copy2(final/'label.txt',staging/'label.txt')
    if display:
        (staging / 'label.txt').write_text(display, encoding='utf-8')
    ensure_closed(fixture)
    if before != auth_fingerprint(official) or before != auth_fingerprint(staging):
        raise ValueError('收录期间登录文件发生变化，原快照已保留。请确认 Claude 已完全退出后重试。')
    old = profiles / ('.studio-previous-'+final.name+'-'+stamp()) if final.exists() else None
    details = {'Final': str(final), 'Staging': str(staging), 'Previous': str(old) if old else '', 'Account': current}
    transaction_state(backup, profiles, dict(details, Status='applying'))
    try:
        if old: replace_within(final,old,profiles)
        replace_within(staging,final,profiles)
        transaction_state(backup, profiles, dict(details, Status='committed'))
    except Exception:
        if old and old.exists() and not final.exists():replace_within(old,final,profiles)
        transaction_state(backup, profiles, dict(details, Status='rollback-needs-review'))
        raise
    return {'Message': '桌面版登录已收录。仅核对本地缓存，登录是否有效请自行打开 Claude 确认。',
            'Backup': str(backup), 'Profile': final.name, 'Account': current}


def official_mode_changes(official, third):
    changes=[]
    for root in (official,third):
        path=root/'claude_desktop_config.json'
        config=read(path)
        config['deploymentMode']='1p'
        enterprise=config.get('enterpriseConfig')
        if root==third and isinstance(enterprise,dict):
            for key in GATEWAY_KEYS:
                enterprise.pop(key,None)
            if not enterprise: config.pop('enterpriseConfig',None)
        changes.append((path,config))
    path=third/'configLibrary/_meta.json'
    meta=read(path)
    ours={PROFILE_ID,'00000000-0000-4000-8000-000000157210'}
    if isinstance(meta.get('entries'),list):
        meta['entries']=[entry for entry in meta['entries'] if entry.get('id') not in ours]
    if meta.get('appliedId') in ours: meta.pop('appliedId',None)
    changes.append((path,meta))
    profile_path=third/'configLibrary'/(PROFILE_ID+'.json')
    if profile_path.exists():
        profile=read(profile_path)
        for key in GATEWAY_KEYS:profile.pop(key,None)
        changes.append((profile_path,profile))
    return changes


def switch_target(request, profiles):
    """Validate the selected snapshot without touching files."""
    name = checked_name(str(request.get('Name', '')))
    selected = profiles / name
    account = read(selected / 'config.json').get('lastKnownAccountUuid')
    if not account or not all((selected / item).exists() for item in AUTH_ITEMS):
        raise ValueError('这个账号没有完整的登录快照，请先登录后保存。')
    if request.get('ExpectedAccount') and request['ExpectedAccount'] != account:
        raise ValueError('登录快照与所选账号不一致，已停止切换。')
    metadata = read(selected / 'studio-profile.json')
    if metadata.get('Account', account) != account or not cached_login(read(selected / 'config.json')):
        raise ValueError('登录快照身份不一致或缺少登录缓存，请自行重新登录并收录。')
    return selected, account


def switch_account(request, data_root, fixture=None):
    ensure_closed(fixture)
    official, third, profiles, _ = app_paths(fixture)
    selected, account = switch_target(request, profiles)
    selected_before = auth_fingerprint(selected)
    outgoing = read(official / 'config.json')
    if (read(official / 'claude_desktop_config.json').get('deploymentMode', '1p') != '3p'
            and outgoing.get('lastKnownAccountUuid') == account and cached_login(outgoing)):
        captured = save_account({'ExpectedAccount': account}, data_root, fixture)
        captured['Message'] = '当前已是所选账号，已更新最新登录快照，请自行打开 Claude 确认。'
        return captured
    checkpoint = None
    if (read(official / 'claude_desktop_config.json').get('deploymentMode', '1p') != '3p'
            and outgoing.get('lastKnownAccountUuid') != account and cached_login(outgoing)):
        checkpoint = save_account({'ExpectedAccount': outgoing.get('lastKnownAccountUuid')}, data_root, fixture)
    backup = snapshot([official / item for item in AUTH_ITEMS] + [official / 'claude_desktop_config.json', third / 'claude_desktop_config.json'], data_root, 'switch-account')
    ensure_closed(fixture)
    transaction_state(backup, profiles, {'Status': 'applying', 'Account': account})
    # Rename live directories on their own volume. The verified long-term backup may be on D:/E:.
    displaced = official / ('.studio-displaced-'+stamp())
    displaced.mkdir()
    transaction_state(backup, profiles, {'Status': 'applying', 'Account': account, 'LocalDisplaced':str(displaced)})
    done = []
    try:
        for item in AUTH_ITEMS:
            target = official / item
            if target.exists():
                replace_within(target, displaced / item,official)
            done.append(item)
            if (selected / item).is_dir():
                shutil.copytree(selected / item, target)
            else:
                shutil.copy2(selected / item, target)
        if selected_before != auth_fingerprint(official) or selected_before != auth_fingerprint(selected):
            raise ValueError('切换期间快照内容发生变化，已停止并恢复原登录文件。')
        patch_files(official_mode_changes(official,third),data_root,fixture)
        transaction_state(backup, profiles, {'Status': 'committed', 'Account': account})
    except Exception:
        for item in reversed(done):
            target = official / item
            if target.exists():
                partial=official/('.studio-partial-'+item.replace(' ','-')+'-'+stamp())
                replace_within(target,partial,official)
            if (displaced / item).exists():
                replace_within(displaced / item, target,official)
        transaction_state(backup, profiles, {'Status': 'rollback-needs-review'})
        raise
    return {'Message': '已恢复所选账号的本地登录状态。请自行打开 Claude 确认登录；对话原文未移动。',
            'Backup': str(backup), 'OutgoingCheckpoint': checkpoint}


def login_new(request, data_root, fixture=None):
    """Prepare a signed-out Desktop so the user can sign in to another account.

    The current login is captured first and its files are moved aside, not signed out, so the
    saved snapshot stays usable. Other Desktop settings in config.json are kept.
    """
    ensure_closed(fixture)
    official, _, profiles, _ = app_paths(fixture)
    if read(official / 'claude_desktop_config.json').get('deploymentMode', '1p') == '3p':
        raise ValueError('当前为第三方模式，请先切换回官方账号，再添加新的官方账号。')
    outgoing = read(official / 'config.json')
    checkpoint = None
    if cached_login(outgoing) and outgoing.get('lastKnownAccountUuid'):
        checkpoint = save_account({'ExpectedAccount': outgoing['lastKnownAccountUuid']}, data_root, fixture)
    cleared = ('Network', 'Local Storage', 'Session Storage', 'config.json')
    backup = snapshot([official / item for item in AUTH_ITEMS], data_root, 'login-new')
    ensure_closed(fixture)
    displaced = official / ('.studio-displaced-' + stamp())
    displaced.mkdir()
    details = {'Action': 'login-new', 'LocalDisplaced': str(displaced)}
    transaction_state(backup, profiles, dict(details, Status='applying'))
    moved = []
    try:
        for item in cleared:
            if (official / item).exists():
                replace_within(official / item, displaced / item, official)
                moved.append(item)
        blank = {key: value for key, value in outgoing.items()
                 if key not in SIGNED_IN_KEYS and not key.startswith('oauth:')}
        atomic_json(official / 'config.json', blank)
        transaction_state(backup, profiles, dict(details, Status='committed'))
    except Exception:
        if 'config.json' in moved and (official / 'config.json').exists():
            replace_within(official / 'config.json', official / ('.studio-partial-config-' + stamp()), official)
        for item in reversed(moved):
            replace_within(displaced / item, official / item, official)
        transaction_state(backup, profiles, dict(details, Status='rolled-back'))
        raise
    return {'Message': '已准备好新账号登录：原账号登录已保存并移到备份，没有在服务器上注销。请在 Claude 登录页登录新账号，然后回到本工具点击「收录当前登录」。',
            'Backup': str(backup), 'OutgoingCheckpoint': checkpoint}


CODEX_ACCOUNT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{7,79}')


def codex_home(fixture=None):
    if fixture:
        return Path(fixture) / 'codex'
    return Path(os.environ.get('CODEX_HOME') or Path(os.environ['USERPROFILE']) / '.codex')


def codex_profiles(fixture=None):
    return Path(fixture) / 'codex-profiles' if fixture else Path(os.environ['USERPROFILE']) / '.codex-profiles'


def jwt_claims(token):
    # Display only: the payload is decoded locally and never treated as verified identity.
    try:
        payload = token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    except Exception:
        return {}


def codex_identity(auth):
    tokens = auth.get('tokens') or {}
    claims = jwt_claims(tokens.get('id_token', ''))
    info = claims.get('https://api.openai.com/auth') or {}
    return {'Account': tokens.get('account_id') or info.get('chatgpt_account_id') or '',
            'Email': claims.get('email', ''), 'Plan': info.get('chatgpt_plan_type', ''),
            'SubscriptionUntil': info.get('chatgpt_subscription_active_until', ''),
            'SignedIn': auth.get('auth_mode') == 'chatgpt' and bool(tokens.get('refresh_token'))}


def protect(folder):
    # Login snapshots contain tokens: only the current Windows user may read the folder.
    if os.name == 'nt':
        identity = subprocess.check_output(['whoami.exe'], creationflags=NO_WINDOW, text=True).strip()
        result = subprocess.run(['icacls.exe', str(folder), '/inheritance:r', '/grant:r', identity + ':(OI)(CI)F'],
                                creationflags=NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode:
            raise ValueError('无法限制登录快照访问权限，已停止。')


def copy_verified(source, target):
    """Replace target with source's bytes atomically and confirm both hashes match."""
    expected = sha(source)
    temporary = target.with_name(target.name + '.' + secrets.token_hex(5) + '.tmp')
    shutil.copy2(source, temporary)
    if sha(temporary) != expected:
        temporary.unlink()
        raise ValueError('复制登录文件时校验失败，已停止。')
    os.replace(temporary, target)
    if sha(target) != expected or sha(source) != expected:
        raise ValueError('登录文件在复制期间发生变化，请确认 Codex 已完全退出后重试。')


def window_label(minutes):
    return {300: '5 小时', 10080: '每周'}.get(minutes) or (f'{minutes // 60} 小时' if minutes and minutes % 60 == 0 else f'{minutes} 分钟')


def rate_windows(limits):
    rows = []
    for key in ('primary', 'secondary'):
        window = (limits or {}).get(key)
        if isinstance(window, dict) and window.get('used_percent') is not None:
            reset = window.get('resets_at')
            rows.append({'Label': window_label(int(window.get('window_minutes') or 0)),
                         'Used': round(float(window['used_percent']), 1),
                         'Resets': datetime.fromtimestamp(reset, timezone(timedelta(hours=8))).strftime('%m-%d %H:%M') if reset else ''})
    return rows


def codex_rate_limits(home, wanted=None, scan=120):
    """Newest rate-limit report per account from the newest sessions that account created."""
    files = sorted((home / 'sessions').rglob('rollout-*.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True)[:scan]
    found = {}
    for path in files:
        try:
            with path.open(encoding='utf-8', errors='replace') as stream:
                account = (json.loads(stream.readline()).get('payload') or {}).get('creator_account_id', '')
                if not account or account in found or (wanted and account not in wanted):
                    continue
                latest = None
                for line in stream:
                    if '"rate_limits"' in line and '"token_count"' in line:
                        entry = json.loads(line)
                        limits = (entry.get('payload') or {}).get('rate_limits')
                        if limits:
                            latest = {'At': entry.get('timestamp', ''), 'Windows': rate_windows(limits),
                                      'Plan': limits.get('plan_type') or ''}
                if latest:
                    found[account] = latest
        except (OSError, ValueError):
            continue
    return found


def codex_save(request, data_root, fixture=None):
    # Reading auth.json is safe while Codex runs; switching, which writes it, still requires Codex closed.
    home, profiles = codex_home(fixture), codex_profiles(fixture)
    live = home / 'auth.json'
    identity = codex_identity(read(live))
    if not identity['SignedIn'] or not CODEX_ACCOUNT.fullmatch(identity['Account']):
        raise ValueError('Codex 当前没有 ChatGPT 账号登录（API Key 模式不收录）。请先在 Codex 中登录后再收录。')
    if request.get('ExpectedAccount') and request['ExpectedAccount'] != identity['Account']:
        raise ValueError('Codex 当前登录的账号与所选卡片不一致。请刷新；要收录其他账号，请先在 Codex 中登录该账号。')
    display = str(request.get('DisplayName', '')).strip()
    if len(display) > 120 or any(ord(c) < 32 for c in display):
        raise ValueError('显示名称请使用 120 字以内的单行文字。')
    target = profiles / identity['Account']
    backup = snapshot([live, target / 'auth.json'], data_root, 'codex-save')
    if not target.exists():
        target.mkdir(parents=True)
        if not fixture:
            protect(target)
    copy_verified(live, target / 'auth.json')
    meta = read(target / 'studio-profile.json')
    meta.update({'Version': 1, 'Account': identity['Account'], 'Email': identity['Email'], 'Plan': identity['Plan'],
                 'SavedAt': stamp(), 'Validation': 'local-file-only'})
    if display:
        meta['DisplayName'] = display
    limits = codex_rate_limits(home, {identity['Account']}).get(identity['Account'])
    if limits:
        meta['RateLimits'] = limits
    atomic_json(target / 'studio-profile.json', meta)
    return {'Message': f'已收录 Codex 账号 {identity["Email"] or identity["Account"]}。仅核对本地文件，登录是否有效请在 Codex 中确认。',
            'Backup': str(backup), 'Account': identity['Account']}


def codex_target(request, fixture=None):
    account = str(request.get('Account', ''))
    if not CODEX_ACCOUNT.fullmatch(account):
        raise ValueError('Codex 账号标识无效，请刷新后重试。')
    folder = codex_profiles(fixture) / account
    identity = codex_identity(read(folder / 'auth.json'))
    if not identity['SignedIn'] or identity['Account'] != account:
        raise ValueError('这个 Codex 账号没有完整的登录快照，请先登录后收录。')
    return folder, identity


def codex_switch(request, data_root, fixture=None):
    ensure_closed(fixture, 'codex')
    home = codex_home(fixture)
    folder, identity = codex_target(request, fixture)
    current = codex_identity(read(home / 'auth.json'))
    if current['SignedIn'] and current['Account'] == identity['Account']:
        # Never put an older snapshot over the live, possibly refreshed, login of the same account.
        saved = codex_save({'ExpectedAccount': identity['Account']}, data_root, fixture)
        saved['Message'] = '当前已是所选 Codex 账号，已更新它的登录快照。'
        return saved
    checkpoint = codex_save({'ExpectedAccount': current['Account']}, data_root, fixture) if current['SignedIn'] else None
    live = home / 'auth.json'
    backup = snapshot([live], data_root, 'codex-switch')
    ensure_closed(fixture, 'codex')
    journal = codex_profiles(fixture)
    transaction_state(backup, journal, {'Status': 'applying', 'Action': 'codex-switch', 'Account': identity['Account']})
    try:
        home.mkdir(parents=True, exist_ok=True)
        copy_verified(folder / 'auth.json', live)
        transaction_state(backup, journal, {'Status': 'committed', 'Action': 'codex-switch', 'Account': identity['Account']})
    except Exception:
        record = read(backup / 'manifest.json')['Records'][0]
        if record['Exists']:
            copy_verified(Path(record['Backup']), live)
        elif live.exists():
            archive_file(live, backup / 'partial-auth.json')
        transaction_state(backup, journal, {'Status': 'rolled-back', 'Action': 'codex-switch'})
        raise
    return {'Message': f'已切换到 Codex 账号 {identity["Email"] or identity["Account"]}。请在 Codex 中确认登录；会话记录本来就在本机共享，没有移动。',
            'Backup': str(backup), 'OutgoingCheckpoint': checkpoint}


def codex_login_new(request, data_root, fixture=None):
    """Save the current Codex login, then move auth.json into the private backup so Codex asks to sign in."""
    ensure_closed(fixture, 'codex')
    live = codex_home(fixture) / 'auth.json'
    current = codex_identity(read(live))
    checkpoint = codex_save({'ExpectedAccount': current['Account']}, data_root, fixture) if current['SignedIn'] else None
    if not live.exists():
        return {'Message': 'Codex 当前已是未登录状态，请在 Codex 中登录新账号后收录。', 'OutgoingCheckpoint': checkpoint}
    backup = snapshot([live], data_root, 'codex-login-new')
    ensure_closed(fixture, 'codex')
    archive_file(live, backup / 'signed-out-auth.json')
    return {'Message': '已准备好登录新 Codex 账号：原账号已收录并移入备份，没有在服务器上注销。请在 Codex 中登录新账号，然后回到本工具点击「收录当前登录」。',
            'Backup': str(backup), 'OutgoingCheckpoint': checkpoint}


def codex_catalog(data_root=None, fixture=None):
    """Read-only account list for the Codex page, with each account's latest quota report."""
    home, profiles = codex_home(fixture), codex_profiles(fixture)
    current = codex_identity(read(home / 'auth.json'))
    accounts = {}
    for folder in sorted(profiles.iterdir()) if profiles.is_dir() else []:
        if folder.name.startswith('.') or not folder.is_dir() or folder.is_symlink():
            continue
        identity = codex_identity(read(folder / 'auth.json'))
        meta = read(folder / 'studio-profile.json')
        if identity['Account'] != folder.name:
            continue
        accounts[folder.name] = {'Account': folder.name, 'Email': identity['Email'] or meta.get('Email', ''),
                                 'Plan': identity['Plan'], 'DisplayName': meta.get('DisplayName', ''),
                                 'SavedAt': meta.get('SavedAt', ''), 'Saved': identity['SignedIn'],
                                 'RateLimits': meta.get('RateLimits')}
    if current['SignedIn'] and CODEX_ACCOUNT.fullmatch(current['Account']):
        row = accounts.setdefault(current['Account'], {'Account': current['Account'], 'Saved': False, 'SavedAt': '',
                                                       'DisplayName': '', 'RateLimits': None})
        row.update({'Email': current['Email'], 'Plan': current['Plan']})
    fresh = codex_rate_limits(home, set(accounts))
    for account, row in accounts.items():
        row['IsCurrent'] = current['SignedIn'] and account == current['Account']
        if account in fresh:
            row['RateLimits'] = fresh[account]
    if data_root:
        # Lets the usage statistics show emails instead of raw account IDs.
        names = read(Path(data_root) / 'codex-accounts.json')
        names.update({a: r['Email'] for a, r in accounts.items() if r.get('Email')})
        atomic_json(Path(data_root) / 'codex-accounts.json', names)
    return {'Accounts': sorted(accounts.values(), key=lambda r: (not r['IsCurrent'], r.get('Email', ''))),
            'SignedIn': current['SignedIn'], 'Current': current['Account'] if current['SignedIn'] else ''}


MCP_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,79}')
BARE_KEY = re.compile(r'[A-Za-z0-9_-]+')


def config_paths(fixture=None):
    user = app_paths(fixture)[3]
    home = codex_home(fixture)
    return {'claude-json': user / '.claude.json', 'codex-toml': home / 'config.toml',
            'claude-skills': user / '.claude/skills', 'codex-skills': home / 'skills'}


def codex_servers(text):
    import tomllib
    return tomllib.loads(text).get('mcp_servers', {}) if text.strip() else {}


def to_codex(config):
    """Claude Code JSON server entry -> Codex TOML table (stdio command or streamable HTTP URL)."""
    if config.get('command'):
        out = {'command': config['command']}
        for key in ('args', 'env', 'cwd'):
            if config.get(key):
                out[key] = config[key]
        return out
    if config.get('url'):
        out = {'url': config['url']}
        if config.get('headers'):
            out['http_headers'] = config['headers']
        return out
    raise ValueError('这个 MCP 服务的配置既没有 command 也没有 url，无法转换。')


def to_claude(config):
    if config.get('command'):
        out = {'type': 'stdio', 'command': config['command'], 'args': config.get('args', []), 'env': config.get('env', {})}
        if config.get('cwd'):
            out['cwd'] = config['cwd']
        return out
    if config.get('url'):
        out = {'type': 'http', 'url': config['url']}
        if config.get('http_headers'):
            out['headers'] = config['http_headers']
        return out
    raise ValueError('这个 MCP 服务的配置既没有 command 也没有 url，无法转换。')


def toml_value(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)  # JSON string escapes are valid TOML basic strings.
    if isinstance(value, list):
        return '[' + ', '.join(toml_value(v) for v in value) + ']'
    if isinstance(value, dict):
        return '{ ' + ', '.join(toml_key(k) + ' = ' + toml_value(v) for k, v in value.items()) + ' }'
    raise ValueError('MCP 配置含有无法写入 TOML 的值。')


def toml_key(key):
    return key if BARE_KEY.fullmatch(key) else json.dumps(key, ensure_ascii=False)


def toml_block(name, config, section='mcp_servers'):
    lines, tables = [f'[{section}.{toml_key(name)}]'], []
    for key, value in config.items():
        if isinstance(value, dict):
            tables.append((key, value))
        else:
            lines.append(f'{toml_key(key)} = {toml_value(value)}')
    for key, value in tables:
        lines += ['', f'[{section}.{toml_key(name)}.{toml_key(key)}]']
        lines += [f'{toml_key(k)} = {toml_value(v)}' for k, v in value.items()]
    return '\n'.join(lines) + '\n'


def toml_without(text, name, section='mcp_servers'):
    """Drop the [<section>.<name>] table and its sub-tables, leaving every other line untouched."""
    names = {re.escape(name), re.escape(json.dumps(name)), re.escape("'" + name + "'")}
    header = re.compile(r'^\s*\[\s*' + re.escape(section) + r'\s*\.\s*(?:' + '|'.join(names) + r')\s*(?:\.[^\]]*)?\]\s*(?:#.*)?$')
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith('['):
            skipping = bool(header.match(line.rstrip('\r\n')))
        if not skipping:
            out.append(line)
    return ''.join(out)


def edit_codex_servers(text, name, config):
    """New config.toml text with one server added (config) or removed (None), verified by parsing."""
    before = codex_servers(text)
    import tomllib
    whole_before = tomllib.loads(text) if text.strip() else {}
    updated = toml_without(text, name)
    if config is not None:
        updated = updated.rstrip('\r\n') + ('\n\n' if updated.strip() else '') + toml_block(name, config)
    whole_after = tomllib.loads(updated)
    expected = dict(whole_before)
    servers = dict(before)
    if config is None:
        servers.pop(name, None)
    else:
        servers[name] = config
    if servers:
        expected['mcp_servers'] = servers
    else:
        expected.pop('mcp_servers', None)
    if whole_after.get('mcp_servers', {}) == {} and 'mcp_servers' in whole_after and not servers:
        whole_after.pop('mcp_servers')
    if whole_after != expected:
        raise ValueError('config.toml 的写法无法安全地自动修改（例如使用了内联表），已停止，文件未改动。')
    return updated


def mcp_catalog(data_root=None, fixture=None):
    paths = config_paths(fixture)
    claude = read(paths['claude-json']).get('mcpServers') or {}
    toml_path = paths['codex-toml']
    codex = codex_servers(toml_path.read_text(encoding='utf-8')) if toml_path.is_file() else {}
    library = read(Path(data_root) / 'mcp-library.json').get('Servers', {}) if data_root else {}
    rows = []
    for name in sorted(set(claude) | set(codex) | set(library), key=str.lower):
        config = claude.get(name) or codex.get(name) or library[name].get('Claude') or library[name].get('Codex') or {}
        summary = config.get('url') or ' '.join([str(config.get('command', ''))] + [str(a) for a in config.get('args', [])][:3])
        rows.append({'Name': name, 'Claude': name in claude, 'Codex': name in codex, 'Kind': 'HTTP' if config.get('url') else 'stdio',
                     'Summary': summary.strip()[:160]})
    return {'Servers': rows, 'ClaudePath': str(paths['claude-json']), 'CodexPath': str(toml_path)}


def mcp_toggle(request, data_root, fixture=None):
    name, app, enabled = str(request.get('Name', '')), request.get('App'), request.get('Enabled') is True
    if not MCP_NAME.fullmatch(name) or app not in ('claude', 'codex'):
        raise ValueError('MCP 服务名称或目标应用无效。')
    paths = config_paths(fixture)
    library_path = Path(data_root) / 'mcp-library.json'
    library = read(library_path)
    entry = library.setdefault('Servers', {}).setdefault(name, {})
    claude_doc = read(paths['claude-json'])
    claude = claude_doc.get('mcpServers') or {}
    toml_path = paths['codex-toml']
    toml_text = toml_path.read_text(encoding='utf-8') if toml_path.is_file() else ''
    codex = codex_servers(toml_text)
    present = name in (claude if app == 'claude' else codex)
    if present == enabled:
        return {'Message': f'{name} 在 {"Claude Code" if app == "claude" else "Codex"} 中已是{"开启" if enabled else "关闭"}状态。'}
    target = paths['claude-json'] if app == 'claude' else toml_path
    backup = snapshot([target], data_root, 'mcp-toggle')
    if app == 'claude':
        if enabled:
            config = entry.get('Claude') or (to_claude(codex[name]) if name in codex else to_claude(entry['Codex']) if entry.get('Codex') else None)
            if not config:
                raise ValueError('没有找到这个 MCP 服务的配置。')
            claude[name] = config
        else:
            entry['Claude'] = claude.pop(name)  # Kept so re-enabling restores the exact original entry.
        claude_doc['mcpServers'] = claude
        atomic_json(target, claude_doc)
        if (name in (read(target).get('mcpServers') or {})) != enabled:
            raise ValueError('~/.claude.json 写入后被其他程序覆盖。请关闭 Claude Code 后重试；原文件已备份。')
    else:
        if enabled:
            config = entry.get('Codex') or (to_codex(claude[name]) if name in claude else to_codex(entry['Claude']) if entry.get('Claude') else None)
            if not config:
                raise ValueError('没有找到这个 MCP 服务的配置。')
        else:
            entry['Codex'] = codex[name]
            config = None
        updated = edit_codex_servers(toml_text, name, config)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.' + secrets.token_hex(5) + '.tmp')
        temporary.write_text(updated, encoding='utf-8')
        os.replace(temporary, target)
    atomic_json(library_path, library)
    where = 'Claude Code' if app == 'claude' else 'Codex'
    return {'Message': f'已在 {where} 中{"开启" if enabled else "关闭"} MCP 服务 {name}。重新启动 {where} 后生效；修改前的文件已备份。',
            'Backup': str(backup)}


def tree_digest(path):
    path = Path(path)
    digest = hashlib.sha256()
    for file in sorted(tree_files(path), key=lambda f: str(f.relative_to(path)).lower()):
        digest.update(str(file.relative_to(path)).replace('\\', '/').encode() + b'\0' + sha(file).encode())
    return digest.hexdigest()


def skill_description(folder):
    skill = Path(folder) / 'SKILL.md'
    try:
        text = skill.read_text(encoding='utf-8-sig', errors='replace')[:4000]
    except OSError:
        return ''
    found = re.search(r'^description:\s*(.+)$', text, re.M)
    return found.group(1).strip().strip('"\'')[:200] if found else ''


def skill_dirs(root):
    if not root.is_dir():
        return {}
    # Codex keeps its own built-in skills in .system and _shared; those are never offered as toggles.
    return {p.name: p for p in root.iterdir() if p.is_dir() and not p.name.startswith(('.', '_'))}


def skills_catalog(data_root=None, fixture=None):
    paths = config_paths(fixture)
    claude, codex = skill_dirs(paths['claude-skills']), skill_dirs(paths['codex-skills'])
    library = skill_dirs(Path(data_root) / 'skills-library') if data_root else {}
    rows = []
    for name in sorted(set(claude) | set(codex) | set(library), key=str.lower):
        source = claude.get(name) or codex.get(name) or library.get(name)
        same = None
        if name in claude and name in codex:
            try:
                same = tree_digest(claude[name]) == tree_digest(codex[name])
            except (OSError, ValueError):
                same = False
        rows.append({'Name': name, 'Claude': name in claude, 'Codex': name in codex, 'Description': skill_description(source),
                     'Differs': same is False})
    return {'Skills': rows, 'ClaudePath': str(paths['claude-skills']), 'CodexPath': str(paths['codex-skills'])}


def skill_toggle(request, data_root, fixture=None):
    name, app, enabled = str(request.get('Name', '')), request.get('App'), request.get('Enabled') is True
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', name) or app not in ('claude', 'codex'):
        raise ValueError('技能名称或目标应用无效。')
    paths = config_paths(fixture)
    roots = {'claude': paths['claude-skills'], 'codex': paths['codex-skills']}
    other = 'codex' if app == 'claude' else 'claude'
    target, library = roots[app] / name, Path(data_root) / 'skills-library' / name
    where = 'Claude Code' if app == 'claude' else 'Codex'
    if target.exists() == enabled:
        return {'Message': f'技能 {name} 在 {where} 中已是{"开启" if enabled else "关闭"}状态。'}
    if enabled:
        source = roots[other] / name if (roots[other] / name).is_dir() else library
        if not source.is_dir():
            raise ValueError('没有找到这个技能的文件。')
        expected = tree_digest(source)
        roots[app].mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
        if tree_digest(target) != expected:
            raise ValueError('复制技能后校验失败，请检查目标文件夹。')
        return {'Message': f'已在 {where} 中开启技能 {name}。重新启动 {where} 后生效。'}
    expected = tree_digest(target)  # Also refuses links inside the skill folder.
    backup = snapshot([target], data_root, 'skill-toggle')
    if not library.is_dir() or tree_digest(library) != expected:
        # The library keeps the disabled copy so it can be turned back on later.
        if library.exists():
            archive = backup / 'previous-library'
            shutil.copytree(library, archive)
            shutil.rmtree(library)
        shutil.copytree(target, library)
        if tree_digest(library) != expected:
            raise ValueError('保存技能副本时校验失败，原技能未移除。')
    if tree_digest(target) != expected:
        raise ValueError('技能在关闭期间被修改，已停止，原技能未移除。')
    shutil.rmtree(target)  # Removed only after verified copies exist in the library and the backup.
    return {'Message': f'已在 {where} 中关闭技能 {name}。副本保存在本工具的技能库，随时可以重新开启。', 'Backup': str(backup)}


OAUTH_ID = re.compile(r'[0-9]{8}-[0-9]{6}-[0-9]{6}-[0-9a-f]{8}')
LOGIN_URL = re.compile(r'https://auth\.openai\.com/\S+')


def codex_cli(fixture=None):
    """The official Codex CLI: the copy bundled with Codex Desktop, else one on PATH."""
    if fixture:
        return read(Path(fixture) / 'codex-cli.json', {}).get('Command')
    bundled = sorted((Path(os.environ.get('LOCALAPPDATA', '')) / 'OpenAI/Codex/bin').glob('*/codex.exe'),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    found = str(bundled[0]) if bundled else shutil.which('codex')
    return [found] if found else None


def oauth_folder(data_root, request):
    identifier = str(request.get('Id', ''))
    if not OAUTH_ID.fullmatch(identifier):
        raise ValueError('授权任务标识无效，请重新开始添加。')
    return Path(data_root) / 'codex-oauth' / identifier


def codex_oauth_start(request, data_root, fixture=None):
    """Run the official `codex login` into a private, temporary CODEX_HOME.

    The browser sign-in happens on OpenAI's own page; the running Codex and its ~/.codex login
    are never touched, and nothing is switched. The token file is filed as a saved account later.
    """
    command = codex_cli(fixture)
    if not command:
        raise ValueError('没有找到 Codex 命令行（Codex 桌面版自带的 codex.exe）。请先安装或更新 Codex。')
    folder = Path(data_root) / 'codex-oauth' / (stamp() + '-' + secrets.token_hex(4))
    (folder / 'home').mkdir(parents=True)
    if not fixture:
        protect(folder)
    environment = dict(os.environ, CODEX_HOME=str(folder / 'home'))
    with (folder / 'login.log').open('wb') as log:
        process = subprocess.Popen(command + ['login'], env=environment, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, creationflags=NO_WINDOW, close_fds=True)
    atomic_json(folder / 'state.json', {'Pid': process.pid, 'Started': stamp(), 'Command': command[0]})
    url = ''
    for _ in range(30):  # The CLI prints the sign-in link and opens the browser within a moment.
        found = LOGIN_URL.search((folder / 'login.log').read_text(encoding='utf-8', errors='replace'))
        if found or process.poll() is not None:
            url = found.group(0) if found else ''
            break
        time.sleep(.2)
    return {'Id': folder.name, 'Url': url,
            'Message': '已在浏览器中打开 OpenAI 官方登录页。请在浏览器中登录要添加的账号，完成后会自动加入列表；当前 Codex 不受影响。'}


def end_login_process(folder, fixture=None):
    state = read(folder / 'state.json')
    pid = state.get('Pid')
    if not pid or fixture:
        return
    # Only end the process this task started: confirm the PID still runs the same executable.
    try:
        text = powershell(f"$p=Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue; if($p){{$p.Path}}")
    except ValueError:
        return
    if text and Path(text).name.lower() == Path(state.get('Command', '')).name.lower():
        subprocess.run(['taskkill.exe', '/PID', str(int(pid)), '/T', '/F'], creationflags=NO_WINDOW,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def codex_oauth_status(request, data_root, fixture=None):
    folder = oauth_folder(data_root, request)
    if not folder.is_dir():
        return {'State': 'missing', 'Message': '授权任务已结束。'}
    auth_path = folder / 'home/auth.json'
    identity = codex_identity(read(auth_path)) if auth_path.is_file() else {'SignedIn': False}
    if identity['SignedIn'] and CODEX_ACCOUNT.fullmatch(identity.get('Account', '')):
        target = codex_profiles(fixture) / identity['Account']
        existed = (target / 'auth.json').is_file()
        backup = snapshot([target / 'auth.json'], data_root, 'codex-oauth') if existed else None
        if not target.exists():
            target.mkdir(parents=True)
            if not fixture:
                protect(target)
        copy_verified(auth_path, target / 'auth.json')
        meta = read(target / 'studio-profile.json')
        meta.update({'Version': 1, 'Account': identity['Account'], 'Email': identity['Email'], 'Plan': identity['Plan'],
                     'SavedAt': stamp(), 'Validation': 'official-cli-login', 'Source': 'background-oauth'})
        atomic_json(target / 'studio-profile.json', meta)
        end_login_process(folder, fixture)
        shutil.rmtree(folder)  # Our own temporary CODEX_HOME; its token now lives in the protected profile.
        verb = '已更新' if existed else '已添加'
        return {'State': 'done', 'Account': identity['Account'], 'Backup': str(backup) if backup else '',
                'Message': f'{verb} Codex 账号 {identity["Email"] or identity["Account"]}。它已保存在后台，需要时在卡片上点「切换账号」。'}
    pid = read(folder / 'state.json').get('Pid')
    running = True
    if fixture:
        running = not (folder / 'exited').exists()
    elif pid:
        try:
            running = bool(powershell(f"if(Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue){{'yes'}}"))
        except ValueError:
            running = True
    log = (folder / 'login.log').read_text(encoding='utf-8', errors='replace') if (folder / 'login.log').exists() else ''
    if not running:
        tail = ' '.join(line.strip() for line in log.splitlines()[-3:] if line.strip() and 'http' not in line)[:200]
        shutil.rmtree(folder)
        return {'State': 'failed', 'Message': '浏览器授权没有完成' + (f'：{tail}' if tail else '。') + ' 可以重新点击「添加账号」。'}
    found = LOGIN_URL.search(log)
    return {'State': 'waiting', 'Url': found.group(0) if found else ''}


def codex_oauth_cancel(request, data_root, fixture=None):
    folder = oauth_folder(data_root, request)
    if folder.is_dir():
        end_login_process(folder, fixture)
        shutil.rmtree(folder)
    return {'Message': '已取消添加 Codex 账号，没有保存任何登录。'}


def bulk_toggle(request, data_root, fixture=None, kind='mcp'):
    """Turn every MCP server or skill on or off for one app, continuing past individual failures."""
    app, enabled = request.get('App'), request.get('Enabled') is True
    if app not in ('claude', 'codex'):
        raise ValueError('目标应用无效。')
    rows = mcp_catalog(data_root, fixture)['Servers'] if kind == 'mcp' else skills_catalog(data_root, fixture)['Skills']
    toggle = mcp_toggle if kind == 'mcp' else skill_toggle
    changed, failed = [], []
    for row in rows:
        if row[app.capitalize()] == enabled:
            continue
        try:
            toggle({'Name': row['Name'], 'App': app, 'Enabled': enabled}, data_root, fixture)
            changed.append(row['Name'])
        except Exception as error:
            failed.append(f'{row["Name"]}（{error}）')
    where = 'Claude Code' if app == 'claude' else 'Codex'
    noun = 'MCP 服务' if kind == 'mcp' else '技能'
    message = f'已在 {where} 中{"开启" if enabled else "关闭"} {len(changed)} 个{noun}。'
    if failed:
        message += f' {len(failed)} 个未完成：' + '；'.join(failed[:3])
    return {'Message': message + ' 重新启动后生效。', 'Changed': changed, 'Failed': failed}


GOOGLE_PRICING_URL = 'https://ai.google.dev/gemini-api/docs/pricing?hl=en'
MONTHS = {m: i for i, m in enumerate(('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
                                      'september', 'october', 'november', 'december'), 1)}
GOOGLE_FIELDS = (('input price', 'Input'), ('output price', 'Output'), ('context caching price', 'CacheRead'))


def page_tokens(markup):
    """Visible text of an HTML page as a list of short pieces, in document order."""
    import html as htmllib
    markup = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', markup)
    return [p for p in (htmllib.unescape(t).strip() for t in re.split(r'<[^>]+>', markup)) if p]


def price_date(text):
    found = re.search(r'([A-Z][a-z]+) (\d{1,2}), (\d{4})', text)
    return f'{int(found.group(3)):04d}-{MONTHS[found.group(1).lower()]:02d}-{int(found.group(2)):02d}' if found and found.group(1).lower() in MONTHS else None


def parse_google_pricing(markup):
    """Paid Standard-tier text prices per model from Google's Gemini API pricing page.

    Each model section names its ID in <code>; only its "Standard" block is read. Prices that change
    on a date become Periods (From/Until), and "prompts > 200k tokens" rows a LongPrompt tier.
    Audio and per-hour storage prices are skipped.
    """
    ids = list(re.finditer(r'<code[^>]*>(gemini-[a-z0-9.\-]+)</code>', markup))
    models = {}
    waiting = []  # IDs named together (e.g. a model and its "-customtools" variant) share the next prices.
    for index, match in enumerate(ids):
        model = match.group(1)
        end = ids[index + 1].start() if index + 1 < len(ids) else len(markup)
        tokens = page_tokens(markup[match.end():end])
        if model in models:
            continue
        if 'Standard' not in tokens:
            waiting.append(model)
            continue
        block = tokens[tokens.index('Standard') + 1:]
        for stop in ('Batch', 'Flex', 'Priority', 'Contributor'):
            if stop in block:
                block = block[:block.index(stop)]
        entries = {}
        field = None
        for token in block:
            lower = token.lower()
            label = next((name for key, name in GOOGLE_FIELDS if lower.startswith(key)), None)
            if label:
                field = label
                continue
            if lower.startswith(('grounding', 'used to improve', 'paid tier', 'free tier')):
                field = None
                continue
            found = re.match(r'\$([0-9]+(?:\.[0-9]+)?)', token)
            if not field or not found or 'per hour' in lower or '(audio)' in lower:
                continue
            entries.setdefault(field, []).append({
                'Price': float(found.group(1)), 'Long': True if re.search(r'>\s*200k', token) else False if re.search(r'<=\s*200k', token) else None,
                'From': price_date(token) if 'starting' in lower else None, 'Until': price_date(token) if 'through' in lower else None})
        if 'Input' not in entries or 'Output' not in entries:
            continue
        spans = sorted({(e['From'], e['Until']) for e in entries['Input']}, key=lambda s: (s[0] or '', s[1] or '9999'))
        periods = []
        for start, until in spans:
            def pick(name, long):
                fits = [e for e in entries.get(name, []) if (e['From'], e['Until']) in ((start, until), (None, None))]
                chosen = [e for e in fits if (e['Long'] is True) == long]
                return chosen[0]['Price'] if chosen else None
            period = {'From': start, 'Until': until}
            for name in ('Input', 'Output', 'CacheRead'):
                if pick(name, False) is not None:
                    period[name] = pick(name, False)
            long = {name: pick(name, True) for name in ('Input', 'Output', 'CacheRead') if pick(name, True) is not None}
            if 'Input' in long:
                period['LongPrompt'] = dict(long, Threshold=200000)
            if 'Input' in period:
                periods.append(period)
        if periods:
            for name in waiting + [model]:
                models.setdefault(name, {'Periods': periods})
                if name.endswith('-preview'):  # Antigravity names e.g. gemini-3.1-pro-low, without "-preview".
                    models.setdefault(name[:-len('-preview')], {'Periods': periods})
        waiting = []
    if len(models) < 5:
        raise ValueError('Google 价格页格式已变化，未更新价格。')
    return models


def update_google_pricing(data_root, fetch=None):
    def browser_fetch(url):
        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 ClaudeSwitchStudio'})
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read(4 * 1024 * 1024).decode('utf-8')
    models = parse_google_pricing((fetch or browser_fetch)(GOOGLE_PRICING_URL))
    fetched = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    atomic_json(Path(data_root) / 'pricing-google.json', {'Source': GOOGLE_PRICING_URL, 'FetchedAt': fetched, 'Models': models})
    return {'Models': len(models), 'FetchedAt': fetched}


def agy_root(fixture=None):
    return Path(fixture) / 'antigravity_tools' if fixture else Path(os.environ['USERPROFILE']) / '.antigravity_tools'


def agy_proxy(fixture=None):
    """Antigravity Tools' local proxy: the one shared credential every agent connects through."""
    proxy = read(agy_root(fixture) / 'gui_config.json').get('proxy') or {}
    port = int(proxy.get('port') or 8045)
    key = str(proxy.get('api_key') or '')
    running = False
    if fixture:
        running = (agy_root(fixture) / 'proxy-running').exists()
    else:
        import socket
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.6):
                running = True
        except OSError:
            running = False
    return {'Port': port, 'Key': key, 'Running': running, 'Enabled': bool(proxy.get('enabled')),
            'AutoStart': bool(proxy.get('auto_start')), 'BaseUrl': f'http://127.0.0.1:{port}'}


def set_top_key(text, key, value):
    """Set or remove a top-level `key = value` line (before the first table header)."""
    lines = text.splitlines(keepends=True)
    header = next((i for i, l in enumerate(lines) if l.lstrip().startswith('[')), len(lines))
    pattern = re.compile(r'^\s*' + re.escape(key) + r'\s*=')
    found = [i for i in range(header) if pattern.match(lines[i])]
    line = None if value is None else f'{toml_key(key)} = {toml_value(value)}\n'
    if found and line:
        lines[found[0]] = line  # Replace in place, so switching back leaves the file as it was.
        found = found[1:]
    kept = [l for i, l in enumerate(lines) if i not in found]
    if line and line not in kept:
        header = next((i for i, l in enumerate(kept) if l.lstrip().startswith('[')), len(kept))
        kept.insert(header, line)
    return ''.join(kept)


def codex_provider_edit(text, name, provider, top):
    """New config.toml text with [model_providers.<name>] set (or removed) and top-level keys set."""
    import tomllib
    whole = tomllib.loads(text) if text.strip() else {}
    expected = json.loads(json.dumps(whole))
    updated = toml_without(text, name, 'model_providers')
    providers = dict(expected.get('model_providers') or {})
    if provider is None:
        providers.pop(name, None)
    else:
        providers[name] = provider
        updated = updated.rstrip('\r\n') + ('\n\n' if updated.strip() else '') + toml_block(name, provider, 'model_providers')
    if providers:
        expected['model_providers'] = providers
    else:
        expected.pop('model_providers', None)
    for key, value in top.items():
        updated = set_top_key(updated, key, value)
        if value is None:
            expected.pop(key, None)
        else:
            expected[key] = value
    after = tomllib.loads(updated)
    if after.get('model_providers') == {} and 'model_providers' not in expected:
        after.pop('model_providers')
    if after != expected:
        raise ValueError('config.toml 的写法无法安全地自动修改，已停止，文件未改动。')
    return updated


# Third-party accounts. Presets follow CC Switch: an Anthropic-compatible address for Claude Desktop
# and Claude Code, an OpenAI-compatible one for model detection, and a Responses address for Codex.
# The addresses stay editable; detecting models with the user's key is what confirms they work.
VENDORS = {
    'deepseek': {'Name': 'DeepSeek', 'Anthropic': 'https://api.deepseek.com/anthropic', 'OpenAI': 'https://api.deepseek.com/v1',
                 'Codex': 'https://api.deepseek.com', 'Site': 'https://platform.deepseek.com/api_keys'},
    'kimi-coding': {'Name': 'Kimi For Coding（会员）', 'Anthropic': 'https://api.kimi.com/coding', 'OpenAI': 'https://api.kimi.com/coding/v1',
                    'Codex': 'https://api.kimi.com/coding/v1', 'Site': 'https://www.kimi.com/coding'},
    'kimi': {'Name': 'Kimi 开放平台（月之暗面）', 'Anthropic': 'https://api.moonshot.cn/anthropic', 'OpenAI': 'https://api.moonshot.cn/v1',
             'Codex': '', 'Site': 'https://platform.moonshot.cn/console/api-keys'},
    'glm': {'Name': '智谱 GLM', 'Anthropic': 'https://open.bigmodel.cn/api/anthropic', 'OpenAI': 'https://open.bigmodel.cn/api/paas/v4',
            'Codex': '', 'Site': 'https://open.bigmodel.cn/usercenter/apikeys'},
    'qwen': {'Name': '通义千问（阿里云百炼）', 'Anthropic': 'https://dashscope.aliyuncs.com/apps/anthropic',
             'OpenAI': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'Codex': '', 'Site': 'https://bailian.console.aliyun.com/'},
    'minimax': {'Name': 'MiniMax', 'Anthropic': 'https://api.minimaxi.com/anthropic', 'OpenAI': 'https://api.minimaxi.com/v1',
                'Codex': '', 'Site': 'https://platform.minimaxi.com/'},
    'openrouter': {'Name': 'OpenRouter', 'Anthropic': 'https://openrouter.ai/api', 'OpenAI': 'https://openrouter.ai/api/v1',
                   'Codex': '', 'Site': 'https://openrouter.ai/settings/keys'},
    'gemini': {'Name': 'Google Gemini API', 'Anthropic': '', 'OpenAI': 'https://generativelanguage.googleapis.com/v1beta/openai',
               'Codex': '', 'Site': 'https://aistudio.google.com/apikey'},
    # Antigravity Tools' local proxy: its key is read from Antigravity Tools when used, never stored here.
    'antigravity': {'Name': 'Antigravity Tools（本机代理）', 'Anthropic': 'http://127.0.0.1:8045', 'OpenAI': 'http://127.0.0.1:8045/v1',
                    'Codex': 'http://127.0.0.1:8045/v1', 'Site': ''},
    'custom': {'Name': '自定义', 'Anthropic': '', 'OpenAI': '', 'Codex': '', 'Site': ''},
}
VENDOR_ID = re.compile(r'v-[0-9a-f]{12}')
MODEL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/@\[\]\-]{0,119}')
MODEL_TIERS = ('Opus', 'Sonnet', 'Haiku')
CLAUDE_CODE_KEYS = ('ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY', 'ANTHROPIC_MODEL',
                    'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
                    'ANTHROPIC_SMALL_FAST_MODEL')
CODEX_PROVIDER = 'studio'


def dpapi(data, protect):
    """Encrypt or decrypt with Windows DPAPI for the current user; keys never sit in vendors.json as text."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]

    def blob(raw):
        buffer = ctypes.create_string_buffer(raw, len(raw))
        return Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    crypt = ctypes.windll.crypt32
    function = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    source, keep = blob(data)
    entropy, keep_entropy = blob(b'ClaudeSwitchStudio.vendor-key')
    output = Blob()
    if not function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output)):
        raise ValueError('无法用 Windows 数据保护加密或解密 API Key。')
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def vendors_path(data_root):
    return Path(data_root) / 'vendors.json'


def vendor_accounts(data_root):
    rows = [r for r in read(vendors_path(data_root)).get('Accounts', []) if isinstance(r, dict) and VENDOR_ID.fullmatch(str(r.get('Id', '')))]
    for row in rows:
        if 'Codex' not in row:  # Earlier builds marked the OpenAI address as Responses-capable instead.
            row['Codex'] = row.get('OpenAI', '') if row.pop('Responses', False) else ''
        row.pop('Responses', None)
    return rows


def vendor_account(data_root, identifier):
    row = next((r for r in vendor_accounts(data_root) if r['Id'] == identifier), None)
    if row is None:
        raise ValueError('这个第三方账号已不存在，请刷新。')
    return row


def vendor_key(row, fixture=None):
    if row.get('LinkedKey'):
        key = agy_proxy(fixture)['Key']
        if not key:
            raise ValueError('Antigravity Tools 的本地代理还没有 API 密钥，请先在 Antigravity Tools 中打开 API 代理。')
        return key
    return dpapi(base64.b64decode(row['Key']), False).decode('utf-8')


def checked_url(value, label):
    value = str(value or '').strip().rstrip('/')
    if not value:
        return ''
    parts = urllib.parse.urlsplit(value)
    local = parts.hostname in ('127.0.0.1', 'localhost', '::1')
    if parts.scheme not in ('https', 'http') or not parts.hostname or (parts.scheme == 'http' and not local) \
            or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError(f'{label}无效：只接受 https 地址（本机代理可用 http），且不能带账号、参数。')
    return value


def checked_model(value, required=False):
    value = str(value or '').strip()
    if not value:
        if required:
            raise ValueError('请先选择要使用的模型。')
        return ''
    if not MODEL_ID.fullmatch(value):
        raise ValueError(f'模型名称“{value[:40]}”包含不支持的字符。')
    return value


def checked_key(value):
    value = str(value or '').strip()
    if not value or len(value) > 512 or any(c.isspace() for c in value) or not value.isprintable():
        raise ValueError('API Key 为空或格式不对，请重新粘贴。')
    return value


def key_hint(key):
    return '…' + key[-4:] if len(key) > 8 else '已保存'


def http_json(url, headers, timeout=12):
    request = urllib.request.Request(url, headers=dict(headers, **{'User-Agent': 'ClaudeSwitchStudio', 'Accept': 'application/json'}))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(4 * 1024 * 1024).decode('utf-8'))


def detect_models(anthropic, openai, key, fetch=None, codex=''):
    """Model IDs the key can use: OpenAI-style /models first, then Anthropic-style /v1/models."""
    fetch = fetch or http_json
    attempts = []
    for label, base in (('OpenAI', openai), ('Codex', codex if codex != openai else '')):
        if base:
            attempts.append((label, base + '/models', {'Authorization': 'Bearer ' + key}))
    if anthropic:
        attempts.append(('Anthropic', anthropic + '/v1/models', {'x-api-key': key, 'Authorization': 'Bearer ' + key,
                                                                    'anthropic-version': '2023-06-01'}))
    if not attempts:
        raise ValueError('请至少填写一个接口地址。')
    problems = []
    for label, url, headers in attempts:
        try:
            value = fetch(url, headers)
        except urllib.error.HTTPError as error:
            # Never echo the response body or the key: status codes are enough to act on.
            problems.append(f'{label} 接口返回 {error.code}' + ('（Key 无效或没有权限）' if error.code in (401, 403) else ''))
            continue
        except (OSError, ValueError) as error:
            problems.append(f'{label} 接口无法连接（{type(error).__name__}）')
            continue
        rows = value.get('data') if isinstance(value, dict) else value
        models = sorted({str(r.get('id')) for r in rows or [] if isinstance(r, dict) and MODEL_ID.fullmatch(str(r.get('id', '')))})
        if models:
            return {'Models': models, 'Via': label}
        problems.append(f'{label} 接口没有返回模型列表')
    raise ValueError('没有检测到可用模型：' + '；'.join(problems) + '。可以检查地址和 Key，或直接手动填写模型名称。')


def vendor_models(request, data_root, fixture=None, fetch=None):
    """Detect models for a new key, or for a saved account (optionally with edited addresses)."""
    row = vendor_account(data_root, request['Id']) if request.get('Id') else {}
    linked = request.get('LinkedKey', row.get('LinkedKey')) is True
    anthropic = checked_url(request.get('Anthropic', row.get('Anthropic', '')), 'Anthropic 接口地址')
    openai = checked_url(request.get('OpenAI', row.get('OpenAI', '')), 'OpenAI 接口地址')
    codex = checked_url(request.get('Codex', row.get('Codex', '')), 'Codex 接口地址')
    if linked:
        key = vendor_key({'LinkedKey': True}, fixture)
    elif request.get('Key'):
        key = checked_key(request['Key'])
    elif row:
        key = vendor_key(row, fixture)
    else:
        raise ValueError('请先填写 API Key。')
    found = detect_models(anthropic, openai, key, fetch, codex)
    found['Message'] = f'检测到 {len(found["Models"])} 个模型（通过 {found["Via"]} 接口）。'
    return found


def route_state(data_root):
    """Which source each app uses, with what was there before, so switching back restores it exactly.

    Connections made by the earlier Antigravity page (agy-connections.json) count as routes to the
    Antigravity Tools account, keeping their saved settings for the restore.
    """
    routes = read(Path(data_root) / 'routes.json')
    for target, value in read(Path(data_root) / 'agy-connections.json').items():
        if isinstance(value, dict) and target in ('claude-code', 'codex'):
            routes.setdefault(target, dict(value, Source='antigravity-legacy', Provider='antigravity'))
    return routes


def save_routes(data_root, routes, target):
    atomic_json(Path(data_root) / 'routes.json', {k: v for k, v in routes.items() if v})
    legacy_path = Path(data_root) / 'agy-connections.json'
    legacy = read(legacy_path)
    if target in legacy:
        legacy.pop(target)
        atomic_json(legacy_path, legacy)


def vendor_label(data_root, source):
    if source == 'antigravity-legacy':
        return 'Antigravity Tools（本机代理）'
    row = next((r for r in vendor_accounts(data_root) if r['Id'] == source), None)
    return row['Name'] if row else '已删除的第三方账号'


def vendor_save(request, data_root, fixture=None):
    """Add or edit a third-party account. The key is encrypted with DPAPI before it is written."""
    rows = vendor_accounts(data_root)
    identifier = str(request.get('Id') or '')
    existing = next((r for r in rows if r['Id'] == identifier), None) if identifier else None
    if identifier and existing is None:
        raise ValueError('这个第三方账号已不存在，请刷新。')
    vendor = str(request.get('Vendor') or (existing or {}).get('Vendor') or '')
    if vendor not in VENDORS:
        raise ValueError('请选择厂商。')
    name = str(request.get('Name') or '').strip() or VENDORS[vendor]['Name']
    if len(name) > 40 or not name.isprintable():
        raise ValueError('名称最多 40 个字符。')
    row = dict(existing or {'Id': 'v-' + secrets.token_hex(6), 'CreatedAt': stamp()})
    row.update({'Vendor': vendor, 'Name': name,
                'Anthropic': checked_url(request.get('Anthropic'), 'Anthropic 接口地址'),
                'OpenAI': checked_url(request.get('OpenAI'), 'OpenAI 接口地址'),
                'Codex': checked_url(request.get('Codex'), 'Codex 接口地址')})
    row.pop('Responses', None)
    if not row['Anthropic'] and not row['OpenAI'] and not row['Codex']:
        raise ValueError('请至少填写一个接口地址。')
    if request.get('LinkedKey') is True:
        row.update({'LinkedKey': True, 'KeyHint': '使用 Antigravity Tools 的代理密钥'})
        row.pop('Key', None)
    elif request.get('Key'):
        key = checked_key(request['Key'])
        row.update({'Key': base64.b64encode(dpapi(key.encode('utf-8'), True)).decode('ascii'), 'KeyHint': key_hint(key)})
        row.pop('LinkedKey', None)
    elif not existing or existing.get('LinkedKey'):
        raise ValueError('请填写 API Key。')
    models = request.get('Models')
    if models is not None:
        row['Models'] = sorted({checked_model(m) for m in models if str(m).strip()})[:400]
    row['Model'] = checked_model(request.get('Model'))
    row['Tiers'] = {tier: checked_model((request.get('Tiers') or {}).get(tier)) for tier in MODEL_TIERS}
    rows = [r for r in rows if r['Id'] != row['Id']] + [row]
    atomic_json(vendors_path(data_root), {'Accounts': rows})
    used = [t for t, v in route_state(data_root).items() if v.get('Source') == row['Id']]
    note = '正在使用它的应用要重新选择一次才会用上新设置。' if used and existing else ''
    return {'Message': ('已更新' if existing else '已添加') + f'第三方账号「{name}」。' + note, 'Id': row['Id']}


def vendor_in_use(data_root, identifier, fixture=None):
    targets = [t for t, v in route_state(data_root).items() if v.get('Source') == identifier]
    official = app_paths(fixture)[0]
    if read(official / 'claude_desktop_config.json').get('deploymentMode') == '3p' and \
            read(Path(data_root) / 'selected-provider.json').get('Id') == 'vendor:' + identifier:
        targets.append('claude-desktop')
    return targets


def vendor_delete(request, data_root, fixture=None):
    row = vendor_account(data_root, str(request.get('Id', '')))
    used = vendor_in_use(data_root, row['Id'], fixture)
    if used:
        names = {'claude-code': 'Claude Code', 'codex': 'Codex', 'claude-desktop': 'Claude Desktop'}
        raise ValueError('「' + row['Name'] + '」正在被 ' + '、'.join(names[t] for t in used) + ' 使用，请先在接入表中换成其他来源。')
    backup = snapshot([vendors_path(data_root)], data_root, 'vendor-delete')
    atomic_json(vendors_path(data_root), {'Accounts': [r for r in vendor_accounts(data_root) if r['Id'] != row['Id']]})
    return {'Message': f'已删除第三方账号「{row["Name"]}」，删除前的账号列表已备份。', 'Backup': str(backup)}


def vendor_for_host(url):
    host = urllib.parse.urlsplit(url).hostname or ''
    path = urllib.parse.urlsplit(url).path
    matches = [(key, p) for key, p in VENDORS.items() for u in (p['Anthropic'], p['OpenAI'], p['Codex'])
               if u and urllib.parse.urlsplit(u).hostname == host]
    # Prefer the preset whose address shares the path too (api.kimi.com/coding is Kimi For Coding).
    matches.sort(key=lambda m: not any(path.startswith(urllib.parse.urlsplit(u).path or '/') and urllib.parse.urlsplit(u).path
                                       for u in (m[1]['Anthropic'], m[1]['OpenAI'], m[1]['Codex']) if u))
    return matches[0][0] if matches else 'custom'


def vendor_import(request, data_root, fixture=None, db=None):
    """Copy CC Switch's Claude and Codex providers into this tool's encrypted account list.

    A Codex provider with the same key as an imported Claude provider fills in that account's Codex
    address instead of becoming a second account. CC Switch itself is opened read-only.
    """
    import tomllib
    path = Path(db or Path(os.environ['USERPROFILE']) / '.cc-switch/cc-switch.db')
    if not path.is_file():
        raise ValueError('没有找到 CC Switch 的数据（~/.cc-switch/cc-switch.db）。')
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)) as connection:
        found = connection.execute("select id,name,settings_config,app_type from providers "
                                   "where app_type in ('claude','claude-desktop','codex')").fetchall()
    found.sort(key=lambda r: r[3] == 'codex')  # Claude first, so Codex rows can merge into them.
    rows = vendor_accounts(data_root)
    keys = {}
    for row in rows:
        try:
            keys[row['Id']] = vendor_key(row, fixture)
        except ValueError:
            pass
    proxy_key = agy_proxy(fixture)['Key']
    added, merged, skipped = [], [], 0
    for identifier, name, config, app in found:
        settings = json.loads(config or '{}') or {}
        models, tiers, model = [], {}, ''
        if app == 'codex':
            key = str((settings.get('auth') or {}).get('OPENAI_API_KEY') or '').strip()
            try:
                toml = tomllib.loads(settings.get('config') or '')
                provider = (toml.get('model_providers') or {}).get(toml.get('model_provider')) or {}
                url = checked_url(provider.get('base_url'), 'Codex 接口地址') if provider.get('wire_api', 'responses') == 'responses' else ''
                key = checked_key(key)
            except (ValueError, tomllib.TOMLDecodeError):
                skipped += 1  # Official ChatGPT logins and providers without an address or key.
                continue
            if not url:
                skipped += 1
                continue
            model = str(toml.get('model') or '')
            models = [str(m.get('model')) for m in ((settings.get('modelCatalog') or {}).get('models') or []) if isinstance(m, dict)]
            address = {'Codex': url}
        else:
            env = settings.get('env') or {}
            try:
                url = checked_url(env.get('ANTHROPIC_BASE_URL'), 'Anthropic 接口地址')
                key = checked_key(str(env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY') or ''))
            except ValueError:
                skipped += 1
                continue
            model = str(env.get('ANTHROPIC_MODEL') or '')
            tiers = {t: str(env.get(f'ANTHROPIC_DEFAULT_{t.upper()}_MODEL') or '') for t in MODEL_TIERS}
            address = {'Anthropic': url}
        models = [m for m in models if MODEL_ID.fullmatch(m)]
        same = next((r for r in rows if keys.get(r['Id']) == key), None)
        if same:
            changed = False
            for field, value in address.items():
                if not same.get(field):
                    same[field], changed = value, True
            if models:
                same['Models'] = sorted(set(same.get('Models') or []) | set(models))
            if model and not same.get('Model') and MODEL_ID.fullmatch(model):
                same['Model'] = model
            if changed:
                merged.append(same['Name'])
            continue
        vendor = vendor_for_host(url)
        preset = VENDORS[vendor]
        row = {'Id': 'v-' + secrets.token_hex(6), 'CreatedAt': stamp(), 'Vendor': vendor, 'Name': str(name or identifier)[:40],
               'Anthropic': '', 'OpenAI': preset['OpenAI'], 'Codex': '', 'Model': model if MODEL_ID.fullmatch(model) else '',
               'Models': sorted(set(models)), 'Tiers': {t: v if MODEL_ID.fullmatch(v) else '' for t, v in tiers.items()},
               'ImportedFrom': 'CC Switch'}
        row.update(address)
        if vendor == 'antigravity' and key == proxy_key:
            row.update({'LinkedKey': True, 'KeyHint': '使用 Antigravity Tools 的代理密钥'})
        else:
            row.update({'Key': base64.b64encode(dpapi(key.encode('utf-8'), True)).decode('ascii'), 'KeyHint': key_hint(key)})
        rows.append(row)
        keys[row['Id']] = key
        added.append(row['Name'])
    atomic_json(vendors_path(data_root), {'Accounts': rows})
    message = f'从 CC Switch 导入了 {len(added)} 个账号' + ('：' + '、'.join(added[:6]) + ('等' if len(added) > 6 else '') if added else '')
    if merged:
        message += f'；为 {"、".join(dict.fromkeys(merged))} 补上了 Codex 或 Claude 接口'
    return {'Message': message + (f'；{skipped} 个官方登录或缺少地址、Key 的条目已跳过' if skipped else '') + '。CC Switch 本身没有改动。'}


def claude_code_settings_path(fixture=None):
    return config_paths(fixture)['claude-json'].parent / '.claude/settings.json'


def route_claude_code(request, data_root, fixture=None):
    """Point Claude Code (CLI and Desktop's Code) at a third-party account, or restore what was there."""
    source = str(request.get('Source', ''))
    path = claude_code_settings_path(fixture)
    settings = read(path)
    env = dict(settings.get('env') or {})
    routes = route_state(data_root)
    entry = routes.get('claude-code')
    if source == 'official':
        if not entry:
            return {'Message': 'Claude Code 已在使用原来的设置，没有修改。'}
        backup = snapshot([path], data_root, 'route-claude-code')
        for key, value in (entry.get('Previous') or {}).items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        routes.pop('claude-code')
        message = 'Claude Code 已恢复接入前的设置（官方登录或你原来的配置）。'
    else:
        row = vendor_account(data_root, source)
        if not row.get('Anthropic'):
            raise ValueError('「' + row['Name'] + '」没有 Anthropic 兼容接口，不能接入 Claude Code。')
        model = checked_model(request.get('Model') or row.get('Model'), True)
        key = vendor_key(row, fixture)
        backup = snapshot([path], data_root, 'route-claude-code')
        previous = dict((entry or {}).get('Previous') or {})
        for name in CLAUDE_CODE_KEYS:
            previous.setdefault(name, env.get(name))  # Values this tool never set are still the originals.
        tiers = row.get('Tiers') or {}
        wanted = {'ANTHROPIC_BASE_URL': row['Anthropic'], 'ANTHROPIC_AUTH_TOKEN': key, 'ANTHROPIC_API_KEY': None,
                  'ANTHROPIC_MODEL': model, 'ANTHROPIC_SMALL_FAST_MODEL': None,
                  **{f'ANTHROPIC_DEFAULT_{t.upper()}_MODEL': tiers.get(t) or None for t in MODEL_TIERS}}
        for name, value in wanted.items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        routes['claude-code'] = {'Source': row['Id'], 'Model': model, 'Previous': previous}
        message = f'Claude Code 已接入「{row["Name"]}」（第三方，模型 {model}）。'
    if env:
        settings['env'] = env
    else:
        settings.pop('env', None)
    atomic_json(path, settings)
    save_routes(data_root, routes, 'claude-code')
    return {'Message': message + '新开的 Claude Code 会话立即生效；已经打开的会话仍用原设置，不会被关闭。', 'Backup': str(backup)}


def write_text_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(5) + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    os.replace(temporary, path)


def codex_route(request, data_root, fixture=None):
    """Point Codex (app and CLI share config.toml) at a third-party account, or back to the official login."""
    import tomllib
    ensure_closed(fixture, 'codex')
    source = str(request.get('Source', ''))
    path = config_paths(fixture)['codex-toml']
    text = path.read_text(encoding='utf-8') if path.is_file() else ''
    current = tomllib.loads(text) if text.strip() else {}
    routes = route_state(data_root)
    entry = routes.get('codex')
    message, backup = '', None
    if source == 'official':
        if entry:
            backup = snapshot([path], data_root, 'codex-route')
            previous = entry.get('Previous') or {}
            text = codex_provider_edit(text, entry.get('Provider', CODEX_PROVIDER), None,
                                       {'model_provider': previous.get('model_provider'), 'model': previous.get('model')})
            write_text_atomic(path, text)
            routes.pop('codex')
            save_routes(data_root, routes, 'codex')
            message = 'Codex 已恢复接入前的模型提供方和模型。'
        if request.get('Account'):
            switched = codex_switch({'Account': request['Account']}, data_root, fixture)
            return dict(switched, Message=(message + switched['Message']).strip())
        return {'Message': message or 'Codex 已在使用官方登录，没有修改。', 'Backup': str(backup) if backup else ''}
    row = vendor_account(data_root, source)
    if not row.get('Codex'):
        raise ValueError('「' + row['Name'] + '」没有填写 Codex（Responses）接口地址，Codex 无法使用。')
    model = checked_model(request.get('Model') or row.get('Model'), True)
    key = vendor_key(row, fixture)
    backup = snapshot([path], data_root, 'codex-route')
    if entry:
        previous = entry.get('Previous') or {}
        if entry.get('Provider', CODEX_PROVIDER) != CODEX_PROVIDER:
            text = codex_provider_edit(text, entry['Provider'], None, {})
    else:
        previous = {k: current.get(k) for k in ('model_provider', 'model')}
    # The key goes in the provider block, so the ChatGPT login in auth.json stays as it is.
    provider = {'name': row['Name'] + '（第三方）', 'base_url': row['Codex'], 'wire_api': 'responses',
                'requires_openai_auth': False, 'experimental_bearer_token': key}
    write_text_atomic(path, codex_provider_edit(text, CODEX_PROVIDER, provider, {'model_provider': CODEX_PROVIDER, 'model': model}))
    routes['codex'] = {'Source': row['Id'], 'Model': model, 'Provider': CODEX_PROVIDER, 'Previous': previous}
    save_routes(data_root, routes, 'codex')
    return {'Message': f'Codex 已接入「{row["Name"]}」（第三方，模型 {model}）。', 'Backup': str(backup)}


def vendor_catalog(data_root, fixture=None):
    """Everything the account page needs about third-party accounts and the access table; never a key."""
    import tomllib
    routes = route_state(data_root)
    accounts = []
    for row in vendor_accounts(data_root):
        public = {k: row.get(k) for k in ('Id', 'Vendor', 'Name', 'Anthropic', 'OpenAI', 'Codex', 'KeyHint', 'Model',
                                           'Tiers', 'Models', 'CreatedAt', 'ImportedFrom')}
        public.update({'VendorName': VENDORS.get(row.get('Vendor'), VENDORS['custom'])['Name'], 'Linked': bool(row.get('LinkedKey')),
                       'Models': row.get('Models') or [], 'Tiers': row.get('Tiers') or {},
                       'CanClaude': bool(row.get('Anthropic')), 'CanCodex': bool(row.get('Codex')),
                       'UsedBy': vendor_in_use(data_root, row['Id'], fixture)})
        accounts.append(public)
    env = read(claude_code_settings_path(fixture)).get('env') or {}
    toml_path = config_paths(fixture)['codex-toml']
    codex = tomllib.loads(toml_path.read_text(encoding='utf-8')) if toml_path.is_file() else {}

    def described(target, outside):
        entry = routes.get(target)
        if entry:
            return {'Source': entry['Source'], 'Label': vendor_label(data_root, entry['Source']), 'Model': entry.get('Model', '')}
        return {'Source': 'external' if outside else 'official', 'Label': outside or '官方', 'Model': ''}

    base = str(env.get('ANTHROPIC_BASE_URL') or '')
    provider = str(codex.get('model_provider') or '')
    official = app_paths(fixture)[0]
    selected = read(Path(data_root) / 'selected-provider.json').get('Id', '')
    desktop_3p = read(official / 'claude_desktop_config.json').get('deploymentMode') == '3p'
    desktop = {'Source': 'official', 'Label': '官方', 'Model': ''}
    if desktop_3p:
        source = selected[len('vendor:'):] if selected.startswith('vendor:') else 'external'
        desktop = {'Source': source, 'Model': '',
                   'Label': vendor_label(data_root, source) if source != 'external' else (selected or '第三方（其他工具设置）')}
    tools = agy_proxy(fixture)
    return {'Presets': [dict(v, Key=k) for k, v in VENDORS.items()], 'Accounts': accounts,
            'Routes': {'claude-desktop': desktop,
                       'claude-code': described('claude-code', ('其他设置（' + (urllib.parse.urlsplit(base).hostname or base) + '）') if base else ''),
                       'codex': described('codex', ('其他提供方（' + provider + '）') if provider and provider != 'openai' else '')},
            'AntigravityTools': {'Installed': agy_root(fixture).is_dir(), 'HasKey': bool(tools['Key']), 'Running': tools['Running']},
            'CcSwitch': (Path(os.environ.get('USERPROFILE', '')) / '.cc-switch/cc-switch.db').is_file() if not fixture else False}


def agy_price(model, google, anthropic, day):
    """Rates for one model on one day: Gemini from Google's table, Claude models from Anthropic's."""
    def longest(table):
        hits = [k for k in table if model == k or model.startswith(k + '-')]
        return table[max(hits, key=len)] if hits else None
    if model.startswith('claude-'):
        row = longest(anthropic)
        return {'Input': row['Input'], 'Output': row['Output'], 'CacheRead': row.get('CacheRead', row['Input'])} if row else None
    row = longest(google)
    if not row:
        return None
    periods = row['Periods']
    fitting = [p for p in periods if (not p['From'] or day >= p['From']) and (not p['Until'] or day <= p['Until'])]
    return (fitting or periods)[-1]


def fmt_tokens(n):
    return str(n) if n < 1000 else f'{n / 1e3:.1f}K' if n < 1e6 else f'{n / 1e6:.2f}M' if n < 1e9 else f'{n / 1e9:.2f}B'


def agy_usage(request, data_root=None, fixture=None):
    """Usage and API-equivalent cost of requests Antigravity Tools served, by account, model and time."""
    import sqlite3
    hours, days = int(request.get('Hours') or 0), int(request.get('Days') if request.get('Days') is not None else 7)
    zone = timezone(timedelta(hours=8))
    now = datetime.now(zone)
    day_view = not hours and days == 1  # "Today": 00:00 to 24:00 of the calendar day, by hour.
    hourly = bool(hours) or day_view
    if hours:
        since = now - timedelta(hours=hours)
    elif days:
        since = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        since = datetime(1970, 1, 1, tzinfo=zone)
    root = Path(__file__).resolve().parent
    note = ''
    if data_root and not fixture:
        stale = lambda p: not p.exists() or time.time() - p.stat().st_mtime > 7 * 86400
        if request.get('RefreshPrices') or stale(Path(data_root) / 'pricing-google.json'):
            try:
                update_google_pricing(data_root)
            except Exception as error:
                note = f'（本次未能更新 Google 价格：{error}）'
    google, anthropic, source = {}, {}, ''
    for folder in (root, Path(data_root) if data_root else root):
        file = read(folder / 'pricing-google.json')
        if file.get('Models'):
            google.update(file['Models']); source = f'Google 官方价格 · 更新于 {file.get("FetchedAt", "")}'
        anthropic.update(read(folder / 'pricing-official.json').get('Models', {}))
    database = agy_root(fixture) / 'token_stats.db'
    rows = []
    if database.is_file():
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)) as connection:
            rows = connection.execute('select timestamp, account_email, model, input_tokens, output_tokens, cached_tokens '
                                      'from token_usage where timestamp >= ?', (int(since.timestamp()),)).fetchall()
    def tally():
        return {'Requests': 0, 'Input': 0, 'Output': 0, 'CacheRead': 0, 'Cost': 0.0, 'Unpriced': False}
    total, accounts, models, buckets = tally(), {}, {}, {}
    for stamp_value, email, model, input_tokens, output_tokens, cached in rows:
        moment = datetime.fromtimestamp(int(stamp_value), zone)
        input_tokens, output_tokens, cached = int(input_tokens or 0), int(output_tokens or 0), int(cached or 0)
        price = agy_price(str(model or ''), google, anthropic, moment.strftime('%Y-%m-%d'))
        cost = None
        if price:
            tier = price.get('LongPrompt') if price.get('LongPrompt') and input_tokens > price['LongPrompt']['Threshold'] else price
            base = tier['Input']
            # Antigravity records input including cached tokens (total = input + output).
            cost = ((input_tokens - cached) * base + cached * tier.get('CacheRead', base) + output_tokens * tier['Output']) / 1e6
        bucket = moment.strftime('%Y-%m-%d %H' if hourly else '%Y-%m-%d')
        for group in (total, accounts.setdefault(email or '未知账号', tally()), models.setdefault(model or '未知模型', tally()),
                      buckets.setdefault(bucket, tally())):
            group['Requests'] += 1; group['Input'] += max(0, input_tokens - cached); group['Output'] += output_tokens
            group['CacheRead'] += cached
            if cost is None:
                group['Unpriced'] = True
            else:
                group['Cost'] += cost
    grand = total['Input'] + total['Output'] + total['CacheRead']
    priced = bool(google or anthropic)
    def view(group, name=''):
        all_tokens = group['Input'] + group['Output'] + group['CacheRead']
        prompt = group['Input'] + group['CacheRead']
        cost = '未配置价格' if not priced else f'${group["Cost"]:.2f}' + ('+' if group['Unpriced'] else '')
        return {'Name': name, 'Requests': group['Requests'], 'Total': all_tokens, 'TotalText': fmt_tokens(all_tokens),
                'Input': group['Input'], 'InputText': fmt_tokens(group['Input']), 'Output': group['Output'], 'OutputText': fmt_tokens(group['Output']),
                'CacheRead': group['CacheRead'], 'CacheReadText': fmt_tokens(group['CacheRead']), 'CacheWrite': 0, 'CacheWriteText': '0',
                'CacheHitPercent': round(group['CacheRead'] * 100 / prompt, 1) if prompt else 0.0,
                'SharePercent': round(all_tokens * 100 / grand, 1) if grand else 0.0, 'CostText': cost, 'QuotaText': ''}
    span = hours or (24 if day_view else min(days, 30) if days else 30)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    trend = []
    for back in range(span):
        if day_view:
            moment = midnight + timedelta(hours=23 - back)
        else:
            moment = now - (timedelta(hours=back) if hours else timedelta(days=back))
        key = moment.strftime('%Y-%m-%d %H' if hourly else '%Y-%m-%d')
        group = buckets.get(key, tally())
        all_tokens = group['Input'] + group['Output'] + group['CacheRead']
        trend.append({'Date': key, 'Label': moment.strftime('%H:00') if hourly else moment.strftime('%m-%d'),
                      'Total': all_tokens, 'TotalText': fmt_tokens(all_tokens), 'Requests': group['Requests']})
    by_total = lambda items: sorted((view(g, n) for n, g in items), key=lambda r: -r['Total'])
    return {'App': 'antigravity', 'Days': days, 'Hours': hours, 'Hourly': hourly,
            'TrendTitle': '今日每小时（0–24 点）' if day_view else '每小时趋势' if hours else '每日趋势',
            'Totals': view(total), 'Accounts': by_total(accounts.items()), 'Models': by_total(models.items()), 'Trend': trend,
            'Priced': priced, 'PriceSource': (source or '未找到 Google 价格表') + note,
            'Note': '来自 Antigravity Tools 记录的代理请求，按账号与模型统计。Gemini 按 Google 官方 Standard 付费价格（按请求当天适用的价格，超过 200K 的提示按长上下文价格），Claude 模型按 Anthropic 官方价格；没有公开价格的模型（如 gpt-oss）不计入，金额后带“+”。这是 API 等价金额，不是账单。'}


# Cloud backup over WebDAV. Every file is encrypted locally before upload (AES-256-CBC from Windows
# CNG plus HMAC-SHA256, keys derived from the user's passphrase with PBKDF2), so the server only
# ever holds ciphertext. Remote names carry the kind, time and a short machine tag, nothing else.
CLOUD_MAGIC = b'CSSB1\n'
CLOUD_FILE = re.compile(r'(settings|session)-(\d{8}-\d{6})-([0-9a-f]{8})(?:-([0-9a-f-]{22}))?\.cssb')
CLOUD_FOLDER = re.compile(r'[A-Za-z0-9._\- 一-鿿]{1,60}')
CLOUD_MODES = ('local', 'webdav', 'both')
CLOUD_CHUNK = 1 << 20


class AesCbc:
    """AES-256-CBC through Windows CNG; the IV buffer carries the chain between chunks."""

    def __init__(self, key, iv, encrypt):
        import ctypes
        self.ctypes, self.encrypt = ctypes, encrypt
        bcrypt = ctypes.windll.bcrypt
        handle, ulong, void = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p
        bcrypt.BCryptOpenAlgorithmProvider.argtypes = [ctypes.POINTER(handle), ctypes.c_wchar_p, ctypes.c_wchar_p, ulong]
        bcrypt.BCryptSetProperty.argtypes = [handle, ctypes.c_wchar_p, void, ulong, ulong]
        bcrypt.BCryptGenerateSymmetricKey.argtypes = [handle, ctypes.POINTER(handle), void, ulong, void, ulong, ulong]
        for name in ('BCryptEncrypt', 'BCryptDecrypt'):
            getattr(bcrypt, name).argtypes = [handle, void, ulong, void, void, ulong, void, ulong, ctypes.POINTER(ulong), ulong]
        bcrypt.BCryptDestroyKey.argtypes = [handle]
        bcrypt.BCryptCloseAlgorithmProvider.argtypes = [handle, ulong]
        self.bcrypt, self.alg, self.key = bcrypt, handle(), handle()
        self.check(bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(self.alg), 'AES', None, 0))
        mode = ctypes.create_unicode_buffer('ChainingModeCBC')
        self.check(bcrypt.BCryptSetProperty(self.alg, 'ChainingMode', mode, ctypes.sizeof(mode), 0))
        self.secret = ctypes.create_string_buffer(key, len(key))
        self.check(bcrypt.BCryptGenerateSymmetricKey(self.alg, ctypes.byref(self.key), None, 0, self.secret, len(key), 0))
        self.iv = ctypes.create_string_buffer(iv, 16)

    @staticmethod
    def check(status):
        if status != 0:
            raise ValueError('Windows 加密组件出错（%08X）。' % (status & 0xFFFFFFFF))

    def update(self, data, final=False):
        c = self.ctypes
        source = c.create_string_buffer(data, len(data)) if data else c.create_string_buffer(1)
        output = c.create_string_buffer(len(data) + 16)
        size = c.c_ulong()
        function = self.bcrypt.BCryptEncrypt if self.encrypt else self.bcrypt.BCryptDecrypt
        self.check(function(self.key, source, len(data), None, self.iv, 16, output, len(data) + 16, c.byref(size), 1 if final else 0))
        return output.raw[:size.value]

    def close(self):
        self.bcrypt.BCryptDestroyKey(self.key)
        self.bcrypt.BCryptCloseAlgorithmProvider(self.alg, 0)


def cloud_keys(passphrase, salt):
    derived = hashlib.pbkdf2_hmac('sha256', passphrase.encode('utf-8'), salt, 300_000, 64)
    return derived[:32], derived[32:]


def cloud_encrypt(source, target, passphrase):
    salt, iv = secrets.token_bytes(16), secrets.token_bytes(16)
    secret, mac_key = cloud_keys(passphrase, salt)
    head = CLOUD_MAGIC + salt + iv
    mac = hmac.new(mac_key, head, 'sha256')
    cipher = AesCbc(secret, iv, True)
    try:
        with open(source, 'rb') as reader, open(target, 'xb') as writer:
            writer.write(head)
            current = reader.read(CLOUD_CHUNK)
            while True:
                following = reader.read(CLOUD_CHUNK)
                block = cipher.update(current, not following)
                mac.update(block)
                writer.write(block)
                if not following:
                    break
                current = following
            writer.write(mac.digest())
    finally:
        cipher.close()


def cloud_decrypt(source, target, passphrase):
    """Check the HMAC over the whole file first, so nothing is decrypted from a tampered file."""
    size = Path(source).stat().st_size
    body = size - len(CLOUD_MAGIC) - 32 - 32
    with open(source, 'rb') as reader:
        head = reader.read(len(CLOUD_MAGIC) + 32)
        if not head.startswith(CLOUD_MAGIC) or body < 16 or body % 16:
            raise ValueError('这不是本工具的云备份文件，或文件已损坏。')
        secret, mac_key = cloud_keys(passphrase, head[len(CLOUD_MAGIC):len(CLOUD_MAGIC) + 16])
        mac = hmac.new(mac_key, head, 'sha256')
        left = body
        while left:
            chunk = reader.read(min(CLOUD_CHUNK, left))
            mac.update(chunk)
            left -= len(chunk)
        if not hmac.compare_digest(mac.digest(), reader.read(32)):
            raise ValueError('加密密码不对，或云端文件已损坏。')
        reader.seek(len(head))
        cipher = AesCbc(secret, head[-16:], False)
        try:
            with open(target, 'xb') as writer:
                left = body
                while left:
                    chunk = reader.read(min(CLOUD_CHUNK, left))
                    left -= len(chunk)
                    writer.write(cipher.update(chunk, not left))
        finally:
            cipher.close()


def machine_tag():
    who = os.environ.get('COMPUTERNAME', '') + '|' + os.environ.get('USERNAME', '')
    return hashlib.sha256(who.encode('utf-8')).hexdigest()[:8]


def cloud_settings(data_root):
    return read(Path(data_root) / 'cloud.json')


def cloud_catalog(data_root, fixture=None):
    config = cloud_settings(data_root)
    return {'Url': config.get('Url', ''), 'User': config.get('User', ''), 'Folder': config.get('Folder', 'ClaudeSwitchStudio'),
            'Mode': config.get('Mode', 'local'), 'Keep': int(config.get('Keep', 10)), 'AutoCheck': config.get('AutoCheck', True) is True,
            'HasPassword': bool(config.get('Password')), 'HasPassphrase': bool(config.get('Passphrase')),
            'LastUpload': config.get('LastUpload', ''), 'Machine': machine_tag()}


def cloud_save(request, data_root, fixture=None):
    config = cloud_settings(data_root)
    mode = str(request.get('Mode', 'local'))
    if mode not in CLOUD_MODES:
        raise ValueError('请选择备份保存位置。')
    folder = str(request.get('Folder') or 'ClaudeSwitchStudio').strip()
    if not CLOUD_FOLDER.fullmatch(folder):
        raise ValueError('远程文件夹名只能包含字母、数字、中文、空格和 . _ -，不能含斜杠。')
    keep = int(request.get('Keep') or 10)
    if not 1 <= keep <= 100:
        raise ValueError('保留份数应在 1 到 100 之间。')
    user = str(request.get('User') or '').strip()
    if len(user) > 200 or not user.isprintable():
        raise ValueError('用户名格式不对。')
    config.update({'Url': checked_url(request.get('Url'), 'WebDAV 地址'), 'User': user, 'Folder': folder, 'Mode': mode, 'Keep': keep,
                   'AutoCheck': request.get('AutoCheck') is not False})
    if request.get('Password'):
        config['Password'] = base64.b64encode(dpapi(str(request['Password']).encode('utf-8'), True)).decode('ascii')
    if request.get('Passphrase'):
        if len(str(request['Passphrase'])) < 8:
            raise ValueError('加密密码至少 8 个字符。忘记它将无法恢复云端备份。')
        config['Passphrase'] = base64.b64encode(dpapi(str(request['Passphrase']).encode('utf-8'), True)).decode('ascii')
    if mode != 'local' and not (config['Url'] and config['User'] and config.get('Password') and config.get('Passphrase')):
        raise ValueError('使用 WebDAV 需要填写地址、用户名、密码和加密密码。')
    atomic_json(Path(data_root) / 'cloud.json', config)
    where = {'local': '只保存在本机', 'webdav': '只保存到 WebDAV（上传成功后本机副本移到回收站）', 'both': '本机和 WebDAV 各保存一份'}[mode]
    return {'Message': f'已保存云备份设置：备份{where}。'}


def cloud_secrets(data_root):
    config = cloud_settings(data_root)
    if not (config.get('Url') and config.get('User') and config.get('Password') and config.get('Passphrase')):
        raise ValueError('请先在备份中心填写并保存 WebDAV 设置。')
    plain = dict(config)
    plain['Password'] = dpapi(base64.b64decode(config['Password']), False).decode('utf-8')
    plain['Passphrase'] = dpapi(base64.b64decode(config['Passphrase']), False).decode('utf-8')
    return plain


def dav(config, method, name='', data=None, headers=None, timeout=120):
    url = config['Url'].rstrip('/') + '/' + urllib.parse.quote(config['Folder']) + '/' + urllib.parse.quote(name)
    token = base64.b64encode(f"{config['User']}:{config['Password']}".encode('utf-8')).decode('ascii')
    request = urllib.request.Request(url, data=data, method=method,
                                     headers=dict({'Authorization': 'Basic ' + token, 'User-Agent': 'ClaudeSwitchStudio'}, **(headers or {})))
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        reason = {401: '用户名或密码不对', 403: '没有权限（坚果云等需要使用「应用密码」）', 404: '地址或文件夹不存在',
                  507: '云端空间不足'}.get(error.code, 'WebDAV 服务器拒绝了请求')
        raise ValueError(f'{reason}（{error.code}）。') from None
    except OSError as error:
        raise ValueError(f'无法连接 WebDAV 服务器（{type(error).__name__}）。') from None


def dav_folder(config):
    try:
        dav(config, 'MKCOL', '', timeout=30).close()
    except ValueError as error:
        if '（405）' not in str(error) and '（301）' not in str(error):  # 405: the folder already exists.
            raise


def dav_list(config):
    import xml.etree.ElementTree as ET
    body = b'<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:"><d:prop><d:getcontentlength/></d:prop></d:propfind>'
    with dav(config, 'PROPFIND', '', body, {'Depth': '1', 'Content-Type': 'application/xml'}, timeout=30) as response:
        tree = ET.fromstring(response.read(8 * 1024 * 1024))
    mine = machine_tag()
    rows = []
    for item in tree.iter('{DAV:}response'):
        name = urllib.parse.unquote((item.findtext('{DAV:}href') or '').rstrip('/').rsplit('/', 1)[-1])
        match = CLOUD_FILE.fullmatch(name)
        if not match:
            continue
        kind, when, machine, backup = match.groups()
        size = int(item.findtext('.//{DAV:}getcontentlength') or 0)
        rows.append({'Name': name, 'Kind': kind, 'Stamp': when, 'Machine': machine, 'ThisMachine': machine == mine,
                     'BackupId': backup or '', 'Bytes': size,
                     'Time': f'{when[:4]}-{when[4:6]}-{when[6:8]} {when[9:11]}:{when[11:13]}'})
    return sorted(rows, key=lambda r: r['Stamp'], reverse=True)


def cloud_list(request, data_root, fixture=None):
    config = cloud_secrets(data_root)
    dav_folder(config)
    rows = dav_list(config)
    return {'Files': rows, 'Message': f'云端有 {len(rows)} 份备份。'}


def zip_folder(folder, target, prefix, extra):
    import zipfile
    with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED) as archive:
        for name, value in extra.items():
            archive.writestr(name, json.dumps(value, ensure_ascii=False, indent=2))
        if folder:
            for path in sorted(Path(folder).rglob('*')):
                if path.is_symlink():
                    raise ValueError('备份中含有链接，已停止上传。')
                if path.is_file():
                    archive.write(path, prefix + path.relative_to(folder).as_posix())


def backup_folder(data_root, identifier):
    if not re.fullmatch(r'[0-9a-f-]{22}', identifier or ''):
        raise ValueError('备份标识无效，请刷新。')
    folder = Path(data_root) / 'backups' / identifier
    if not (folder / 'manifest.json').is_file() or folder.is_symlink():
        raise ValueError('这份本地备份已不存在，请刷新。')
    return folder


def settings_package(data_root, fixture=None):
    """Tool settings for another machine: DPAPI keys only open here, so keys travel inside the encrypted file."""
    accounts = []
    for row in vendor_accounts(data_root):
        portable = {k: v for k, v in row.items() if k != 'Key'}
        if not row.get('LinkedKey'):
            try:
                portable['PlainKey'] = vendor_key(row, fixture)
            except ValueError:
                continue
        accounts.append(portable)
    package = {'manifest.json': {'Kind': 'settings', 'Machine': machine_tag(), 'Created': stamp()}, 'vendors.json': {'Accounts': accounts}}
    for name in ('preferences.json', 'pricing.json'):
        if (Path(data_root) / name).is_file():
            package[name] = read(Path(data_root) / name)
    return package


@contextmanager
def cloud_workspace(data_root):
    folder = Path(data_root) / 'cloud-temp' / secrets.token_hex(6)
    folder.mkdir(parents=True)
    try:
        yield folder
    finally:
        for path in sorted(folder.rglob('*'), reverse=True):  # Only files this call created.
            path.unlink() if path.is_file() else path.rmdir()
        folder.rmdir()


def cloud_put(config, path, name):
    with open(path, 'rb') as stream:
        dav(config, 'PUT', name, stream, {'Content-Length': str(path.stat().st_size), 'Content-Type': 'application/octet-stream'}, timeout=600).close()


def cloud_upload(request, data_root, fixture=None):
    """Upload the settings package, plus one local backup when BackupId is given, then prune old copies."""
    config = cloud_secrets(data_root)
    dav_folder(config)
    when = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')
    mine = machine_tag()
    uploaded = []
    with cloud_workspace(data_root) as work:
        parts = [('settings', None, settings_package(data_root, fixture), f'settings-{when}-{mine}.cssb')]
        if request.get('BackupId'):
            folder = backup_folder(data_root, request['BackupId'])
            meta = {'Kind': 'session', 'Machine': mine, 'Created': stamp(), 'BackupId': folder.name}
            parts.append(('session', folder, {'manifest-cloud.json': meta}, f'session-{when}-{mine}-{folder.name}.cssb'))
        for kind, folder, extra, name in parts:
            plain, sealed = work / (kind + '.zip'), work / (kind + '.cssb')
            zip_folder(folder, plain, 'backup/', extra)
            cloud_encrypt(plain, sealed, config['Passphrase'])
            cloud_put(config, sealed, name)
            uploaded.append((name, sealed.stat().st_size))
    removed = 0
    rows = dav_list(config)
    for kind in ('settings', 'session'):
        mine_rows = [r for r in rows if r['Kind'] == kind and r['ThisMachine']]
        for row in mine_rows[int(config.get('Keep', 10)):]:
            dav(config, 'DELETE', row['Name'], timeout=60).close()
            removed += 1
    stored = cloud_settings(data_root)
    stored['LastUpload'] = when
    atomic_json(Path(data_root) / 'cloud.json', stored)
    size = sum(s for _, s in uploaded)
    return {'Uploaded': [n for n, _ in uploaded], 'Message': f'已加密上传 {len(uploaded)} 个文件（{size / 1048576:.1f} MB）到 WebDAV'
            + (f'，按保留份数删除了 {removed} 份旧备份' if removed else '') + '。'}


def safe_members(archive):
    for name in archive.namelist():
        parts = Path(name).parts
        if name.startswith(('/', '\\')) or '..' in parts or ':' in name:
            raise ValueError('云端备份中含有不安全的路径，已停止。')
    return archive.namelist()


def cloud_restore(request, data_root, fixture=None):
    """Download one cloud file and bring it back: settings merge into this machine, a session backup
    becomes a local backup card again. Nothing local is overwritten."""
    import zipfile
    name = str(request.get('Name', ''))
    if not CLOUD_FILE.fullmatch(name):
        raise ValueError('云端文件名无效，请刷新列表。')
    config = cloud_secrets(data_root)
    with cloud_workspace(data_root) as work:
        sealed, plain = work / 'download.cssb', work / 'download.zip'
        with dav(config, 'GET', name, timeout=600) as response, open(sealed, 'xb') as writer:
            shutil.copyfileobj(response, writer, CLOUD_CHUNK)
        cloud_decrypt(sealed, plain, config['Passphrase'])
        with zipfile.ZipFile(plain) as archive:
            members = safe_members(archive)
            if 'vendors.json' in members:
                incoming = json.loads(archive.read('vendors.json')).get('Accounts', [])
                rows = vendor_accounts(data_root)
                known = {r['Id'] for r in rows}
                added = 0
                for row in incoming:
                    if not isinstance(row, dict) or not VENDOR_ID.fullmatch(str(row.get('Id', ''))) or row['Id'] in known:
                        continue
                    key = row.pop('PlainKey', None)
                    if key:
                        row['Key'] = base64.b64encode(dpapi(checked_key(key).encode('utf-8'), True)).decode('ascii')
                    elif not row.get('LinkedKey'):
                        continue
                    rows.append(row)
                    added += 1
                atomic_json(vendors_path(data_root), {'Accounts': rows})
                return {'Message': f'已从云端恢复设置：新增 {added} 个第三方账号（本机已有的账号保持不变，Key 已用本机的 Windows 数据保护重新加密）。'}
            meta = json.loads(archive.read('manifest-cloud.json'))
            identifier = str(meta.get('BackupId', ''))
            if not re.fullmatch(r'[0-9a-f-]{22}', identifier):
                raise ValueError('云端备份缺少有效的备份标识。')
            target = Path(data_root) / 'backups' / identifier
            if target.exists():
                return {'Message': '本机已经有这份备份，没有重复下载。'}
            staging = work / 'backup'
            for member in members:
                if member.startswith('backup/') and not member.endswith('/'):
                    destination = staging / member[len('backup/'):]
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, open(destination, 'xb') as writer:
                        shutil.copyfileobj(source, writer, CLOUD_CHUNK)
            if not (staging / 'manifest.json').is_file():
                raise ValueError('云端备份内容不完整，已停止。')
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, target)
    return {'Message': '已把云端备份下载到本机，备份中心里会出现这份备份。'}


def cloud_check(request, data_root, fixture=None):
    """At startup: is there a backup from another machine newer than the last one seen? Never raises."""
    config = cloud_settings(data_root)
    if config.get('Mode', 'local') == 'local' or config.get('AutoCheck', True) is not True:
        return {'Newer': []}
    try:
        rows = [r for r in dav_list(cloud_secrets(data_root)) if not r['ThisMachine']]
    except ValueError as error:
        return {'Newer': [], 'Problem': str(error)}
    seen = str(config.get('LastSeen', ''))
    newer = [r for r in rows if r['Stamp'] > seen]
    if rows:
        config['LastSeen'] = max(seen, rows[0]['Stamp'])
        atomic_json(Path(data_root) / 'cloud.json', config)
    return {'Newer': newer}



# Desktop's Local Storage is a Chromium LevelDB store. Only what sharing the sidebar folders needs:
# read the newest value of each key, and append one write batch to the current log, which LevelDB
# replays the next time Desktop opens the store. Tables are read-only here and never rewritten.
LDB_BLOCK = 32768
LDB_MAGIC = 0xDB4775248B80FB57
LS_PREFIX = b'_https://claude.ai\x00'
SECTIONS_KEY = 'LSS-persisted.dframe-code-sections'
_CRC_TABLE = []
for _n in range(256):
    for _ in range(8):
        _n = (_n >> 1) ^ (0x82F63B78 if _n & 1 else 0)
    _CRC_TABLE.append(_n)


def masked_crc(data):
    crc = 0xFFFFFFFF
    for byte in data:
        crc = _CRC_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    crc ^= 0xFFFFFFFF
    return (((crc >> 15) | (crc << 17)) + 0xA282EAD8) & 0xFFFFFFFF


def varint(data, pos):
    result = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, pos
        shift += 7


def put_varint(number):
    out = bytearray()
    while number >= 0x80:
        out.append(number & 0x7F | 0x80)
        number >>= 7
    out.append(number)
    return bytes(out)


def log_records(data):
    """Whole records of a LevelDB log, and the offset after the last intact one."""
    pos, pending, records, end = 0, b'', [], 0
    while pos + 7 <= len(data):
        left = LDB_BLOCK - pos % LDB_BLOCK
        length, kind = int.from_bytes(data[pos + 4:pos + 6], 'little'), data[pos + 6]
        if left < 7 or (kind == 0 and length == 0):
            pos += left if left < 7 else 7
            continue
        body = data[pos + 7:pos + 7 + length]
        if len(body) < length or masked_crc(bytes([kind]) + body) != int.from_bytes(data[pos:pos + 4], 'little'):
            break
        pos += 7 + length
        if kind == 1:
            records.append(body)
            end = pos
        elif kind == 2:
            pending = body
        elif kind == 3:
            pending += body
        elif kind == 4:
            records.append(pending + body)
            pending, end = b'', pos
    return records, end


def batch_entries(record):
    seq, count, pos = int.from_bytes(record[:8], 'little'), int.from_bytes(record[8:12], 'little'), 12
    for offset in range(count):
        kind = record[pos]
        size, pos = varint(record, pos + 1)
        key, pos, value = record[pos:pos + size], pos + size, None
        if kind == 1:
            size, pos = varint(record, pos)
            value, pos = record[pos:pos + size], pos + size
        yield seq + offset, key, value


def unsnappy(data):
    size, pos = varint(data, 0)
    out = bytearray()
    while pos < len(data):
        tag = data[pos]
        pos += 1
        if tag & 3 == 0:
            length = tag >> 2
            if length >= 60:
                extra = length - 59
                length, pos = int.from_bytes(data[pos:pos + extra], 'little'), pos + extra
            out += data[pos:pos + length + 1]
            pos += length + 1
            continue
        if tag & 3 == 1:
            length, offset, pos = 4 + ((tag >> 2) & 7), ((tag >> 5) << 8) | data[pos], pos + 1
        elif tag & 3 == 2:
            length, offset, pos = (tag >> 2) + 1, int.from_bytes(data[pos:pos + 2], 'little'), pos + 2
        else:
            length, offset, pos = (tag >> 2) + 1, int.from_bytes(data[pos:pos + 4], 'little'), pos + 4
        if not 0 < offset <= len(out):
            raise ValueError('Local Storage 数据块损坏，未修改文件夹。')
        for _ in range(length):
            out.append(out[-offset])
    if len(out) != size:
        raise ValueError('Local Storage 数据块损坏，未修改文件夹。')
    return bytes(out)


def block_entries(block):
    restarts = int.from_bytes(block[-4:], 'little')
    end, pos, key = len(block) - 4 - 4 * restarts, 0, b''
    while pos < end:
        shared, pos = varint(block, pos)
        unshared, pos = varint(block, pos)
        size, pos = varint(block, pos)
        key = key[:shared] + block[pos:pos + unshared]
        pos += unshared
        yield key, block[pos:pos + size]
        pos += size


def table_block(data, handle, pos=0):
    offset, pos = varint(handle, pos)
    size, pos = varint(handle, pos)
    raw = data[offset:offset + size]
    return (unsnappy(raw) if data[offset + size] == 1 else raw), pos


def table_entries(data):
    footer = data[-48:]
    if len(footer) < 48 or int.from_bytes(footer[-8:], 'little') != LDB_MAGIC:
        raise ValueError('Local Storage 表文件无法识别，未修改文件夹。')
    _, pos = varint(footer, 0)
    _, pos = varint(footer, pos)  # Skip the metaindex handle.
    index, _ = table_block(data, footer, pos)
    for _, handle in block_entries(index):
        block, _ = table_block(data, handle)
        for internal, value in block_entries(block):
            tag = int.from_bytes(internal[-8:], 'little')
            yield tag >> 8, internal[:-8], value if tag & 0xFF == 1 else None


def edit_last_sequence(record):
    pos, last = 0, 0
    while pos < len(record):
        tag, pos = varint(record, pos)
        if tag in (2, 3, 4, 9):
            value, pos = varint(record, pos)
            last = value if tag == 4 else last
        elif tag in (1, 5, 6, 7):
            for _ in range({1: 0, 5: 1, 6: 2, 7: 3}[tag]):
                _, pos = varint(record, pos)
            for _ in range({1: 1, 5: 1, 6: 0, 7: 2}[tag]):
                size, pos = varint(record, pos)
                pos += size
        else:
            raise ValueError('Local Storage 清单无法识别，未修改文件夹。')
    return last


def ldb_files(folder):
    return [p for p in Path(folder).iterdir() if p.is_file() and not p.is_symlink() and re.fullmatch(r'\d{6,}\.(ldb|sst|log)', p.name)]


def ldb_state(folder):
    """Newest value of each live key and the highest sequence number in the store."""
    folder, newest, last = Path(folder), {}, 0
    manifest = (folder / 'CURRENT').read_text(encoding='ascii').strip() if (folder / 'CURRENT').is_file() else ''
    if not re.fullmatch(r'MANIFEST-\d{6,}', manifest):
        raise ValueError('Local Storage 缺少清单，未修改文件夹。')
    for record in log_records((folder / manifest).read_bytes())[0]:
        last = max(last, edit_last_sequence(record))
    for path in ldb_files(folder):
        data = path.read_bytes()
        rows = (e for r in log_records(data)[0] for e in batch_entries(r)) if path.suffix == '.log' else table_entries(data)
        for seq, key, value in rows:
            last = max(last, seq)
            if key not in newest or seq > newest[key][0]:
                newest[key] = (seq, value)
    return {key: value for key, (_, value) in newest.items() if value is not None}, last


def ldb_put(folder, items, last):
    """Append one batch to the newest log and verify LevelDB will read it back."""
    logs = sorted((p for p in ldb_files(folder) if p.suffix == '.log'), key=lambda p: int(p.stem))
    if not logs:
        raise ValueError('Local Storage 缺少日志文件，未修改文件夹。')
    path = logs[-1]
    original = path.read_bytes()
    if original[log_records(original)[1]:].strip(b'\x00'):
        raise ValueError('Local Storage 日志末尾不完整，未修改文件夹。')
    data = bytearray((last + 1).to_bytes(8, 'little') + len(items).to_bytes(4, 'little'))
    for key, value in items:
        data += b'\x01' + put_varint(len(key)) + key + put_varint(len(value)) + value
    out, pos, first, data = bytearray(), len(original), True, bytes(data)
    while True:
        left = LDB_BLOCK - pos % LDB_BLOCK
        if left < 7:
            out += b'\x00' * left
            pos += left
            continue
        chunk, data = data[:left - 7], data[left - 7:]
        kind = (1 if not data else 2) if first else (4 if not data else 3)
        out += masked_crc(bytes([kind]) + chunk).to_bytes(4, 'little') + len(chunk).to_bytes(2, 'little') + bytes([kind]) + chunk
        pos, first = pos + 7 + len(chunk), False
        if not data:
            break
    with path.open('r+b') as stream:
        stream.seek(len(original))
        stream.write(out)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
    records = log_records(path.read_bytes())[0]
    if not records or int.from_bytes(records[-1][:8], 'little') != last + 1:
        with path.open('r+b') as stream:
            stream.truncate(len(original))
        raise ValueError('Local Storage 写入校验失败，已恢复原文件。')


def ls_key(name):
    return LS_PREFIX + b'\x01' + name.encode('latin-1')


def ls_text(value):
    return value[1:].decode('utf-16-le') if value[:1] == b'\x00' else value[1:].decode('latin-1')


def ls_value(text):
    try:
        return b'\x01' + text.encode('latin-1')
    except UnicodeEncodeError:
        return b'\x00' + text.encode('utf-16-le')


SESSION_FILE = re.compile(r'local_([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.json')
# Index fields tied to the source account: remote bridges, scheduled tasks and its connectors.
ACCOUNT_SCOPED = ('scheduledTaskId', 'remoteMcpServersConfig')
# Fields every copy of a conversation shares, each group taken whole from one copy: the title and the
# working folder from the copy edited since the last sync, the progress from the most recent copy.
TITLE_FIELDS = ('title', 'titleSource', 'titleTurn', 'previousTitles')
EDITED_FIELDS = (TITLE_FIELDS, ('cwd', 'originCwd', 'gitAnchorsFolderRealpath', 'requestedAdditionalFolders', 'shellFolders'))
PROGRESS_FIELDS = ('cliSessionId', 'priorCliSessionIds', 'lastActivityAt', 'completedTurns', 'lastAssistantUuid',
                   'lastTurnEdgeAt', 'latestUserFrameAt')
# Sidebar folders, kept per "account/organization" scope inside Local Storage.
SIDEBAR_KEYS = ('LSS-persisted.dframe-code-sections', 'LSS-persisted.dframe-group-scopes')


def fields(value, keys):
    return {key: value[key] for key in keys if key in value}


def with_fields(value, keys, chosen):
    value = {key: item for key, item in value.items() if key not in keys}
    value.update(chosen)
    return value


def edited_fields(rows, keys, baseline):
    """Fields of the copy changed since the last sync; before any sync, a manual, often renamed title wins."""
    def rank(row):
        value = row[1]
        title = keys is TITLE_FIELDS
        return (fields(value, keys) != baseline, title and value.get('titleSource') == 'user',
                len(value.get('previousTitles') or []) if title else 0, row[2])
    return fields(max(rows, key=rank)[1], keys)


def index_copies(registry):
    """Each account's organization folders, and every readable conversation index by file name."""
    organizations, copies = {}, {}
    for account in sorted(registry.iterdir()) if registry.is_dir() else []:
        if account.is_symlink() or not account.is_dir():
            continue
        try:
            uuid.UUID(account.name)
        except ValueError:
            continue
        for org in sorted(p for p in account.iterdir() if p.is_dir() and not p.is_symlink()):
            sessions = [f for f in org.iterdir() if SESSION_FILE.fullmatch(f.name) and f.is_file() and not f.is_symlink()]
            organizations.setdefault(account.name, []).append((org, bool(sessions)))
            for path in sessions:
                try:
                    value, digest = read(path), sha(path)
                except (OSError, ValueError):
                    continue
                if value.get('sessionId') == path.stem:
                    copies.setdefault(path.name, []).append((path, value, path.stat().st_mtime_ns, digest))
    return organizations, copies


def sidebar_candidates(stores):
    """Per sidebar key, every scope's folders in every Local Storage copy: (written at, viewed last, layout)."""
    found = {}
    for folder, account in stores:
        try:
            values = ldb_state(folder)[0]
            frame = json.loads(ls_text(values[ls_key('dframe-store')])) if ls_key('dframe-store') in values else {}
            viewed = (frame.get('state') or {}).get('lastSidebarScopeKey')
            for name in SIDEBAR_KEYS:
                if ls_key(name) not in values:
                    continue
                item = json.loads(ls_text(values[ls_key(name)]))
                for scope, layout in (item.get('value') or {}).items():
                    own = scope == viewed or (viewed is None and bool(account) and scope.startswith(account + '/'))
                    found.setdefault(name, []).append((item.get('timestamp') or 0, own, layout))
        except (OSError, ValueError, IndexError, AttributeError):
            continue  # An unreadable saved copy only means it cannot be a source.
    return found


def merged_layout(primary, others):
    """The primary folders plus folders only the others have; conversations already filed stay put."""
    layout = json.loads(json.dumps(primary))
    if isinstance(layout.get('sections'), list):
        filed = {m for s in layout['sections'] for m in s.get('members') or []}
        for other in others:
            for section in other.get('sections') or []:
                if section.get('kind') == 'manual' and section.get('id') not in {s.get('id') for s in layout['sections']}:
                    members = [m for m in section.get('members') or [] if m not in filed]
                    layout['sections'].append(dict(section, members=members))
                    filed.update(members)
    if isinstance(layout.get('groups'), list):
        for other in others:
            known = {g.get('id') for g in layout['groups']}
            layout['groups'] += [g for g in other.get('groups') or [] if g.get('id') not in known]
            for member, group in (other.get('assignments') or {}).items():
                layout.setdefault('assignments', {}).setdefault(member, group)
            for group, members in (other.get('order') or {}).items():
                layout.setdefault('order', {}).setdefault(group, members)
    return layout


def sync_sidebar(official, profiles, scopes, backup, baseline):
    """Give every account scope in the live Local Storage the same sidebar folders.

    The source is a scope edited after the last sync, preferring the one viewed last in the most
    recently written copy. Before any sync, the folders of all accounts are merged so none is lost.
    Returns the changed keys and the new baseline: each key's layout and the moment it was written.
    """
    live = official / 'Local Storage' / 'leveldb'
    if not (live / 'CURRENT').is_file():
        return [], baseline
    stores = [(live, read(official / 'config.json').get('lastKnownAccountUuid'))]
    for profile in sorted(profiles.iterdir()) if profiles.is_dir() else []:
        folder = profile / 'Local Storage' / 'leveldb'
        if not profile.name.startswith('.') and not profile.is_symlink() and (folder / 'CURRENT').is_file():
            stores.append((folder, read(profile / 'config.json').get('lastKnownAccountUuid')))
    values, last = ldb_state(live)
    items, result = [], {}
    for name, candidates in sidebar_candidates(stores).items():
        before = baseline.get(name)
        rank = lambda row: (row[0], row[1])
        if before is None:
            primary = max(candidates, key=rank)[2]
            layout = merged_layout(primary, [row[2] for row in sorted(candidates, key=rank, reverse=True)])
        else:
            edited = [row for row in candidates if row[0] > before['Moment'] and row[2] != before['Layout']]
            layout = max(edited, key=rank)[2] if edited else before['Layout']
        current = json.loads(ls_text(values[ls_key(name)])) if ls_key(name) in values else {}
        known = current.get('value') or {}
        wanted = {scope: layout for scope in set(known) | scopes}
        if wanted != known:
            current.update({'value': wanted, 'timestamp': int(time.time() * 1000)})
            items.append((name, ls_value(json.dumps(current, ensure_ascii=False, separators=(',', ':')))))
        result[name] = {'Layout': layout, 'Moment': current.get('timestamp') or 0}
    if items:
        shutil.copytree(live, backup)
        ldb_put(live, [(ls_key(name), value) for name, value in items], last)
    return [name for name, _ in items], result


def write_index(path, value, digest=None):
    """Create an index, or replace one only while it still holds what was read."""
    if digest is None:
        with path.open('x', encoding='utf-8') as stream:  # 'x' never replaces a file Desktop wrote meanwhile.
            json.dump(value, stream, ensure_ascii=False)
            stream.flush(); os.fsync(stream.fileno())
        return True
    if sha(path) != digest:
        return False
    temporary = path.with_name(path.name + '.' + secrets.token_hex(5) + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    return True


def sync_conversations(data_root, fixture=None):
    """Give every official account the same conversation list, titles, folders and sidebar folders.

    Desktop keeps one index file per conversation under each account's organization folder, while
    all transcripts live in the shared ~/.claude/projects. Missing indexes are created, deletion markers
    are respected, and differing copies take the title and folder edited last; every replaced index
    and the Local Storage holding the sidebar folders are copied aside first, and each step is journaled.
    """
    ensure_closed(fixture)
    official, _, profiles, user = app_paths(fixture)
    transcripts = {p.stem for p in (user / '.claude/projects').glob('*/*.jsonl')}
    organizations, copies = index_copies(official / 'claude-code-sessions')
    state_path = Path(data_root) / 'conversation-sync-state.json'
    state = read(state_path)
    unified, updates = {}, []
    for name, rows in copies.items():
        sources = [row for row in rows if row[1].get('cliSessionId') in transcripts]
        if not sources:
            continue  # Only list conversations whose transcript exists on this computer.
        merged = dict(max(sources, key=lambda row: row[1].get('lastActivityAt') or 0)[1])
        baseline = (state.get('Conversations') or {}).get(name) or [None] * len(EDITED_FIELDS)
        for keys, before in zip(EDITED_FIELDS, baseline):
            merged = with_fields(merged, keys, edited_fields(rows, keys, before))
        unified[name] = merged
        for path, value, _, digest in rows:
            target = value
            for keys in EDITED_FIELDS + (PROGRESS_FIELDS,):
                target = with_fields(target, keys, fields(merged, keys))
            if target != value:
                updates.append((path, target, digest))
    plan = []
    for account, orgs in organizations.items():
        # Desktop shows the organization that already holds this account's conversations.
        targets = [org for org, used in orgs if used] or [org for org, _ in orgs]
        for org in targets:
            for name, value in unified.items():
                raw = SESSION_FILE.fullmatch(name).group(1)
                if (org / name).exists() or (org / f'deleted_{raw}').exists():
                    continue
                copy = {key: item for key, item in value.items() if key not in ACCOUNT_SCOPED}
                if 'bridgeSessionIds' in copy:
                    copy['bridgeSessionIds'] = []
                plan.append((org / name, copy))
    moment = stamp()
    journal = Path(data_root) / 'conversation-sync' / (moment + '.json')
    originals = Path(data_root) / 'conversation-sync-originals' / moment
    created = [{'Path': str(target)} for target, _ in plan]
    updated = [{'Path': str(path), 'Backup': str(originals / path.relative_to(official))} for path, _, _ in updates]
    record = {'Status': 'applying', 'Created': created, 'Updated': updated}
    if plan or updates:
        atomic_json(journal, record)
    for entry, (target, value) in zip(created, plan):
        write_index(target, value)
        entry['Sha256'] = sha(target)
    for entry, (path, value, digest) in zip(updated, updates):
        Path(entry['Backup']).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, entry['Backup'])
        entry['Skipped'] = not write_index(path, value, digest)
        entry['Sha256'] = sha(path)
    record['Updated'] = [entry for entry in updated if not entry['Skipped']]
    scopes = {f'{account}/{org.name}' for account, orgs in organizations.items() for org, used in orgs if used}
    sidebar = state.get('Sidebar') or {}
    try:
        folders, sidebar = sync_sidebar(official, profiles, scopes, originals / 'Local Storage', sidebar)
        if folders:
            record['Sidebar'] = {'Keys': folders, 'Backup': str(originals / 'Local Storage')}
    except (OSError, ValueError, IndexError) as error:
        folders, record['SidebarError'] = [], str(error)
    if plan or updates or folders or 'SidebarError' in record:
        record['Status'] = 'committed'
        atomic_json(journal, record)
    atomic_json(state_path, {'Conversations': {name: [fields(value, keys) for keys in EDITED_FIELDS] for name, value in unified.items()},
                             'Sidebar': sidebar})
    parts = []
    if created:
        parts.append(f'补齐 {len(created)} 条对话到 {len({Path(e["Path"]).parent.parent.name for e in created})} 个账号的列表')
    if record['Updated']:
        parts.append(f'统一了 {len(record["Updated"])} 个对话条目的标题、目录或进度')
    if folders:
        parts.append('统一了各账号的侧栏文件夹')
    message = ('已' + '，'.join(parts) + '。') if parts else '各账号的对话列表已一致。'
    if 'SidebarError' in record:
        message += f' 侧栏文件夹未同步：{record["SidebarError"]}'
    result = {'Created': len(created), 'Updated': len(record['Updated']), 'Folders': bool(folders), 'Message': message}
    if plan or updates or folders or 'SidebarError' in record:
        result['Journal'] = str(journal)
    return result


def get_provider(identifier, db=None, data_root=None, fixture=None):
    if identifier.startswith('vendor:') and data_root:
        # One of this tool's third-party accounts, offered to Claude Desktop as a direct gateway.
        account = vendor_account(data_root, identifier[len('vendor:'):])
        if not account.get('Anthropic'):
            raise ValueError('「' + account['Name'] + '」没有 Anthropic 兼容接口，不能接入 Claude Desktop。')
        return {'id': identifier, 'name': account['Name'], 'meta': {'claudeDesktopMode': 'direct'},
                'config': {'env': {'ANTHROPIC_BASE_URL': account['Anthropic'], 'ANTHROPIC_AUTH_TOKEN': vendor_key(account, fixture)}}}
    row = next((r for r in provider_rows(db) if r['id'] == identifier), None)
    if row is None:
        raise ValueError('提供商已不存在，请刷新列表。')
    if not public_provider(row)['CanSwitch']:
        raise ValueError('仅支持已配置的 Anthropic 兼容端点。')
    return row


def provider_patch(row, third):
    env = row['config']['env']
    path = third / 'configLibrary' / (PROFILE_ID + '.json')
    profile = read(path)
    for key in GATEWAY_KEYS:
        profile.pop(key, None)
    profile.update({'inferenceProvider': 'gateway', 'inferenceGatewayBaseUrl': env['ANTHROPIC_BASE_URL'].rstrip('/'),
                    'inferenceGatewayApiKey': env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY'),
                    'inferenceGatewayAuthScheme': 'bearer', 'disableDeploymentModeChooser': True})
    return path, profile


def switch_provider(request, data_root, fixture=None, db=None):
    ensure_closed(fixture)
    official, third, _, _ = app_paths(fixture)
    row = get_provider(str(request.get('ProviderId', '')), db, data_root, fixture)
    profile_path, profile = provider_patch(row, third)
    outgoing = read(official / 'config.json')
    if read(official / 'claude_desktop_config.json').get('deploymentMode', '1p') != '3p' and cached_login(outgoing):
        save_account({'ExpectedAccount': outgoing.get('lastKnownAccountUuid')}, data_root, fixture)
    changes = []
    # Model mapping uses a local relay; upstream credentials are read from SQLite in memory.
    if row['meta'].get('claudeDesktopMode') == 'proxy':
        routes = row['meta'].get('claudeDesktopModelRoutes', {})
        if not isinstance(routes, dict) or not routes:
            raise ValueError('该提供商缺少模型映射，不能自动切换。')
        relay = read(Path(data_root) / 'relay.json')
        port = int(relay.get('Port', 18941))
        token = secrets.token_urlsafe(32)
        config = {'ProviderId': row['id'], 'Port': port, 'GatewayToken': token}
        if db:
            config['Database'] = str(db)
        changes.append((Path(data_root) / 'relay.json', config))
        profile['inferenceGatewayBaseUrl'] = f'http://127.0.0.1:{port}'
        profile['inferenceGatewayApiKey'] = token
        profile['inferenceModels'] = [{'name': name, 'labelOverride': value.get('labelOverride', value['model']),
                                       'supports1m': bool(value.get('supports1m')), 'prefer1m': bool(value.get('supports1m'))}
                                      for name, value in routes.items()]
        profile['modelDiscoveryEnabled'] = False
    for root in (official, third):
        path = root / 'claude_desktop_config.json'
        config = read(path)
        config['deploymentMode'] = '3p'
        changes.append((path, config))
    meta_path = third / 'configLibrary/_meta.json'
    meta = read(meta_path)
    entries = meta.get('entries', [])
    if not isinstance(entries, list):
        raise ValueError('第三方配置索引格式不兼容，已停止。')
    entries = [entry for entry in entries if entry.get('id') != PROFILE_ID]
    entries.append({'id': PROFILE_ID, 'name': 'Claude Switch Studio'})
    meta.update({'entries': entries, 'appliedId': PROFILE_ID})
    changes += [(profile_path, profile), (meta_path, meta),
                (Path(data_root) / 'selected-provider.json', {'Id': row['id'], 'Gateway': profile['inferenceGatewayBaseUrl']})]
    ready = (lambda: start_relay(data_root)) if row['meta'].get('claudeDesktopMode') == 'proxy' and not fixture else None
    backup = patch_files(changes, data_root, fixture, ready_callback=ready)
    return {'Message': f'Claude Desktop 已切换到第三方「{row["name"]}」。原文、记忆及官方账号登录均未搬动，可随时在接入表切回官方账号。',
            'Backup': str(backup)}


def start_relay(data_root):
    cfg = read(Path(data_root) / 'relay.json')
    request = urllib.request.Request(f'http://127.0.0.1:{cfg["Port"]}/_studio_health',
                                     headers={'Authorization': 'Bearer ' + cfg['GatewayToken']})
    try:
        with urllib.request.urlopen(request, timeout=1) as response:
            if json.load(response).get('service') == 'ClaudeSwitchStudio':
                return
    except (OSError, ValueError):
        pass
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'relay', '--data-root', str(data_root)],
                     creationflags=NO_WINDOW, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(.2)
        try:
            with urllib.request.urlopen(request, timeout=.5) as response:
                if json.load(response).get('service') == 'ClaudeSwitchStudio':
                    return
        except (OSError, ValueError):
            pass
    raise ValueError('配置已保存，但本地模型映射服务未启动，请检查端口占用。可使用时间戳备份恢复。')


def relay_handler(data_root):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args):
            pass  # Never log prompts, headers, URLs with credentials, or provider errors.
        def reply(self, code, value):
            body = json.dumps(value).encode('utf-8')
            self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def authorized(self, cfg):
            supplied = self.headers.get('Authorization', '').removeprefix('Bearer ') or self.headers.get('x-api-key', '')
            return hmac.compare_digest(supplied, cfg.get('GatewayToken', '!invalid!'))
        def do_GET(self):
            cfg = read(Path(data_root) / 'relay.json')
            if not self.authorized(cfg):
                return self.reply(401, {'error': 'unauthorized'})
            if self.path == '/_studio_health':
                return self.reply(200, {'service': 'ClaudeSwitchStudio'})
            row = get_provider(cfg['ProviderId'], cfg.get('Database'))
            models = row['meta'].get('claudeDesktopModelRoutes', {})
            if self.path.rstrip('/') in ('/v1/models', '/models'):
                return self.reply(200, {'data': [{'id': k, 'type': 'model', 'display_name': v.get('labelOverride', k)} for k, v in models.items()]})
            self.reply(404, {'error': 'unsupported path'})
        def do_POST(self):
            cfg = read(Path(data_root) / 'relay.json')
            if not self.authorized(cfg):
                return self.reply(401, {'error': 'unauthorized'})
            if self.path == '/_studio_shutdown':
                self.reply(200, {'service':'ClaudeSwitchStudio','stopping':True})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            if self.path not in ('/v1/messages', '/v1/messages/count_tokens'):
                return self.reply(404, {'error': 'unsupported path'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length <= 0 or length > 32 * 1024 * 1024:
                    return self.reply(413, {'error': 'invalid body length'})
                body = json.loads(self.rfile.read(length))
                row = get_provider(cfg['ProviderId'], cfg.get('Database'))
                route = row['meta'].get('claudeDesktopModelRoutes', {}).get(body.get('model'))
                if not route:
                    return self.reply(400, {'error': 'model not mapped'})
                body['model'] = route['model']
                env = row['config']['env']
                endpoint = env['ANTHROPIC_BASE_URL'].rstrip('/')
                path = self.path[3:] if endpoint.endswith('/v1') else self.path
                token = env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY')
                headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token,
                           'x-api-key': token, 'anthropic-version': self.headers.get('anthropic-version', '2023-06-01')}
                if self.headers.get('anthropic-beta'):
                    headers['anthropic-beta'] = self.headers['anthropic-beta']
                upstream = urllib.request.urlopen(urllib.request.Request(endpoint + path, json.dumps(body).encode(), headers), timeout=180)
            except urllib.error.HTTPError as error:
                return self.reply(error.code, {'error': 'upstream rejected request', 'status': error.code})
            except Exception:
                return self.reply(502, {'error': 'upstream unavailable'})
            try:
                self.send_response(upstream.status)
                self.send_header('Content-Type', upstream.headers.get('Content-Type', 'application/json'))
                self.send_header('Transfer-Encoding', 'chunked'); self.end_headers()
                while chunk := upstream.read1(8192):
                    self.wfile.write(f'{len(chunk):X}\r\n'.encode() + chunk + b'\r\n'); self.wfile.flush()
                self.wfile.write(b'0\r\n\r\n')
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                upstream.close()
    return Handler


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('catalog', 'processes', 'quit', 'launch', 'pricing-update', 'codex-catalog',
                                           'mcp-catalog', 'skills-catalog', 'vendor-catalog', 'cloud-catalog', 'agy-usage', 'execute', 'relay'))
    parser.add_argument('--request'); parser.add_argument('--response'); parser.add_argument('--data-root')
    parser.add_argument('--fixture'); parser.add_argument('--database'); parser.add_argument('--target')
    parser.add_argument('--app', choices=tuple(APPS), default='claude')
    args = parser.parse_args()
    # quit/launch let the Rust core wrap a conversation backup; the UI only sends quit after confirmation.
    readers = {
        'pricing-update': lambda: update_openai_pricing(args.data_root) if args.app == 'codex' else update_google_pricing(args.data_root) if args.target == 'google' else update_pricing(args.data_root),
        'quit': lambda: {'Target': quit_desktop(args.fixture, app=args.app)},
        'launch': lambda: {'Launched': launch_desktop(json.loads(args.target or 'null'), args.fixture, args.app)},
        'codex-catalog': lambda: codex_catalog(args.data_root, args.fixture),
        'mcp-catalog': lambda: mcp_catalog(args.data_root, args.fixture),
        'skills-catalog': lambda: skills_catalog(args.data_root, args.fixture),
        'vendor-catalog': lambda: vendor_catalog(args.data_root, args.fixture),
        'cloud-catalog': lambda: cloud_catalog(args.data_root, args.fixture),
        'agy-usage': lambda: agy_usage(json.loads(args.target or '{}'), args.data_root, args.fixture),
    }
    if args.action in readers:
        try:
            print(json.dumps(dict(readers[args.action](), Ok=True), ensure_ascii=False)); return
        except Exception as error:
            print(json.dumps({'Ok': False, 'Error': str(error)}, ensure_ascii=False)); sys.exit(1)
    if args.action == 'catalog':
        print(json.dumps(catalog(args.database, args.fixture, args.data_root), ensure_ascii=False)); return
    if args.action == 'processes':
        print(json.dumps(process_summary(args.fixture, args.app), ensure_ascii=False)); return
    if args.action == 'relay':
        config = read(Path(args.data_root) / 'relay.json')
        server = http.server.ThreadingHTTPServer(('127.0.0.1', int(config['Port'])), relay_handler(args.data_root))
        try: server.serve_forever()
        finally: server.server_close()
        return
    request = read(args.request)
    if any(k in request for k in ('Key', 'Password', 'Passphrase')):
        # The UI passes newly typed secrets in the request file: drop them from disk before anything else.
        atomic_json(args.request, {k: v for k, v in request.items() if k not in ('Key', 'Password', 'Passphrase')})
    try:
        response = {'Ok': True, 'Result': execute(request, Path(args.data_root), args.fixture, args.database)}
    except Exception as error:
        # No upstream tokens or response bodies are interpolated into adapter exceptions.
        response = {'Ok': False, 'Error': str(error)}
    atomic_json(args.response, response)


PRICING_URL = 'https://platform.claude.com/docs/en/about-claude/pricing.md'
PRICE_COLUMNS = ('Input', 'CacheWrite5m', 'CacheWrite1h', 'CacheRead', 'Output')


def parse_pricing(markdown):
    """Model prices in USD per million tokens from the official pricing page's markdown.

    Display names map to API IDs by the documented pattern (Claude Opus 5.5 -> claude-opus-5-5).
    Haiku 5.5's long-prompt row becomes a LongPrompt tier. Raises when the table looks wrong, so
    a changed page never replaces the previous good prices.
    """
    section = markdown.split('## Model pricing', 1)
    if len(section) < 2:
        raise ValueError('官方价格页中没有找到模型价格表。')
    models = {}
    for line in section[1].split('\n## ', 1)[0].splitlines():
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) != 6 or not cells[0].startswith('Claude '):
            continue
        prices = []
        for cell in cells[1:]:
            found = re.match(r'\$([0-9]+(?:\.[0-9]+)?)\s*/\s*MTok', cell)
            if not found:
                raise ValueError('官方价格表格式已变化，未更新价格。')
            prices.append(float(found.group(1)))
        name = re.sub(r'\(\[[^\]]*\]\([^)]*\)\)', '', cells[0])  # "([retired …](link))" notes
        long_prompt = 'over 100,000 tokens' in name
        name = re.sub(r'\([^)]*\)', '', name).strip()
        if not re.fullmatch(r'Claude [A-Za-z]+ [0-9]+(?:\.[0-9]+)?', name):
            continue
        model = name.lower().replace(' ', '-').replace('.', '-')
        row = dict(zip(PRICE_COLUMNS, prices))
        if long_prompt:
            models.setdefault(model, {})['LongPrompt'] = dict(row, Threshold=100000)
        else:
            models[model] = dict(models.get(model, {}), **row)
    if len(models) < 5 or any('Input' not in v for v in models.values()):
        raise ValueError('官方价格表内容不完整，未更新价格。')
    return models


OPENAI_PRICING_URL = 'https://developers.openai.com/api/docs/pricing.md'
OPENAI_COLUMNS = {'input': 'Input', 'short context input': 'Input', 'output': 'Output', 'short context output': 'Output',
                  'cached input': 'CacheRead', 'short context cached input': 'CacheRead',
                  'short context 30m write': 'CacheWrite30m', 'short context 12h write': 'CacheWrite12h'}
OPENAI_LONG = {'long context input': 'Input', 'long context output': 'Output', 'long context cached input': 'CacheRead',
               'long context 30m write': 'CacheWrite30m', 'long context 12h write': 'CacheWrite12h'}
TIERS = ('Standard', 'Batch', 'Flex', 'Fast', 'Ultrafast', 'Priority')


def parse_openai_pricing(markdown):
    """Standard-tier text-model prices (USD per million tokens) from OpenAI's pricing markdown.

    A bare "Standard"/"Batch"/"Flex"/"Fast" line selects the tier of the tables that follow, and
    "Prices per 1M tokens." starts a new section. Rows split into short/long context get a
    LongPrompt tier above 272K input tokens. The first Standard price seen for a model wins.
    """
    models, tier, header = {}, 'Standard', None
    for raw in markdown.splitlines():
        line = raw.strip()
        if line in TIERS:
            tier, header = line, None
            continue
        if line.startswith('Prices per 1M tokens'):
            tier, header = 'Standard', None
            continue
        if not line.startswith('|'):
            header = None if line else header
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        if cells and cells[0] in ('Model', 'Category') and 'Model' in cells:
            header = [c.lower() for c in cells]
            if any(c in header for c in ('modality', 'training', 'price per minute', 'use case', 'estimated cost')):
                header = None
            continue
        if header is None or tier != 'Standard' or set(line) <= set('|-: ') or len(cells) != len(header):
            continue
        model = re.sub(r'\s*\(.*\)\s*$', '', cells[header.index('model')]).strip()
        if not re.fullmatch(r'[a-z0-9][a-z0-9.\-]*', model) or model in models:
            continue
        def price(cell):
            found = re.fullmatch(r'\$([0-9]+(?:\.[0-9]+)?)', cell)
            return float(found.group(1)) if found else None
        row, long = {}, {}
        for index, name in enumerate(header):
            value = price(cells[index])
            if value is None:
                continue
            if name in OPENAI_COLUMNS:
                row[OPENAI_COLUMNS[name]] = value
            elif name in OPENAI_LONG:
                long[OPENAI_LONG[name]] = value
        if 'Input' not in row or 'Output' not in row:
            continue
        if 'Input' in long:
            row['LongPrompt'] = dict(long, Threshold=272000)
        models[model] = row
    if len(models) < 5:
        raise ValueError('OpenAI 价格表内容不完整或格式已变化，未更新价格。')
    return models


def fetch_text(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'ClaudeSwitchStudio', 'Accept': 'text/markdown'})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read(2 * 1024 * 1024).decode('utf-8')


def update_openai_pricing(data_root, fetch=None):
    models = parse_openai_pricing((fetch or fetch_text)(OPENAI_PRICING_URL))
    fetched = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    atomic_json(Path(data_root) / 'pricing-openai.json', {'Source': OPENAI_PRICING_URL, 'FetchedAt': fetched, 'Models': models})
    return {'Models': len(models), 'FetchedAt': fetched}


def update_pricing(data_root, fetch=None):
    """Refresh data/pricing-official.json from the official docs; fast-mode rates are kept from before."""
    if fetch is None:
        fetch = fetch_text
    models = parse_pricing(fetch(PRICING_URL))
    path = Path(data_root) / 'pricing-official.json'
    previous = read(path, {}).get('Models', {}) or read(Path(__file__).with_name('pricing-official.json'), {}).get('Models', {})
    for model, value in models.items():
        if 'Fast' in previous.get(model, {}):
            value['Fast'] = previous[model]['Fast']
    fetched = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    atomic_json(path, {'Source': PRICING_URL, 'FetchedAt': fetched, 'Models': models})
    return {'Models': len(models), 'FetchedAt': fetched}


def record_timeline(data_root, fixture=None):
    """Append which identity Desktop uses from now on; usage statistics attribute requests by it."""
    official = app_paths(fixture)[0]
    mode = read(official / 'claude_desktop_config.json').get('deploymentMode', '1p')
    path = Path(data_root) / 'account-timeline.json'
    rows = read(path, [])
    provider = ''
    if mode == '3p':
        provider = read(Path(data_root) / 'selected-provider.json').get('Id', '')
        try:  # Show the provider's name in statistics; its ID is the fallback.
            if provider.startswith('vendor:'):
                provider = vendor_label(data_root, provider[len('vendor:'):])
            elif not fixture:
                provider = next((r['name'] for r in provider_rows() if r['id'] == provider), provider)
        except Exception:
            pass
    rows.append({'Time': int(time.time() * 1000), 'Mode': mode,
                 'Account': read(official / 'config.json').get('lastKnownAccountUuid', '') if mode != '3p' else '',
                 'Provider': provider})
    atomic_json(path, rows[-2000:])


ACTIONS = {
    'save': lambda r, d, f, db: save_account(r, d, f),
    'switch': lambda r, d, f, db: switch_account(r, d, f),
    'login-new': lambda r, d, f, db: login_new(r, d, f),
    'provider-switch': switch_provider,
    'sync-conversations': lambda r, d, f, db: sync_conversations(d, f),
    'codex-save': lambda r, d, f, db: codex_save(r, d, f),
    'codex-switch': lambda r, d, f, db: codex_switch(r, d, f),
    'codex-login-new': lambda r, d, f, db: codex_login_new(r, d, f),
    'mcp-toggle': lambda r, d, f, db: mcp_toggle(r, d, f),
    'skill-toggle': lambda r, d, f, db: skill_toggle(r, d, f),
    'mcp-bulk': lambda r, d, f, db: bulk_toggle(r, d, f, 'mcp'),
    'skill-bulk': lambda r, d, f, db: bulk_toggle(r, d, f, 'skill'),
    'codex-oauth-start': lambda r, d, f, db: codex_oauth_start(r, d, f),
    'codex-oauth-status': lambda r, d, f, db: codex_oauth_status(r, d, f),
    'codex-oauth-cancel': lambda r, d, f, db: codex_oauth_cancel(r, d, f),
    'vendor-save': lambda r, d, f, db: vendor_save(r, d, f),
    'vendor-delete': lambda r, d, f, db: vendor_delete(r, d, f),
    'vendor-models': lambda r, d, f, db: vendor_models(r, d, f),
    'vendor-import': lambda r, d, f, db: vendor_import(r, d, f, db),
    'route-claude-code': lambda r, d, f, db: route_claude_code(r, d, f),
    'codex-route': lambda r, d, f, db: codex_route(r, d, f),
    'cloud-save': lambda r, d, f, db: cloud_save(r, d, f),
    'cloud-list': lambda r, d, f, db: cloud_list(r, d, f),
    'cloud-upload': lambda r, d, f, db: cloud_upload(r, d, f),
    'cloud-restore': lambda r, d, f, db: cloud_restore(r, d, f),
    'cloud-check': lambda r, d, f, db: cloud_check(r, d, f),
}
# Actions that never need an app to be closed: configuration edits, background sign-in, and
# reading the current Codex login (a copy of auth.json is taken while Codex may keep running).
CONFIG_ACTIONS = ('mcp-toggle', 'skill-toggle', 'mcp-bulk', 'skill-bulk', 'codex-oauth-start', 'codex-oauth-status',
                  'codex-oauth-cancel', 'codex-save', 'vendor-save', 'vendor-delete', 'vendor-models', 'vendor-import',
                  'route-claude-code', 'cloud-save', 'cloud-list', 'cloud-upload', 'cloud-restore', 'cloud-check')


def preflight(request, fixture=None, db=None, data_root=None):
    """Refuse an operation that would fail anyway before Desktop is closed for it."""
    official, _, profiles, _ = app_paths(fixture)
    if request['Action'] == 'save':
        capture_target(request, official)
    elif request['Action'] == 'switch':
        switch_target(request, profiles)
    elif request['Action'] == 'provider-switch':
        get_provider(str(request.get('ProviderId', '')), db, data_root, fixture)
    elif request['Action'] == 'login-new' and read(official / 'claude_desktop_config.json').get('deploymentMode', '1p') == '3p':
        raise ValueError('当前为第三方模式，请先切换回官方账号，再添加新的官方账号。')
    elif request['Action'] == 'codex-switch' or (request['Action'] == 'codex-route' and request.get('Account')):
        codex_target(request, fixture)
    elif request['Action'] == 'codex-route' and request.get('Source') != 'official':
        row = vendor_account(data_root, str(request.get('Source', '')))
        if not row.get('Codex'):
            raise ValueError('「' + row['Name'] + '」没有填写 Codex（Responses）接口地址，Codex 无法使用。')
        checked_model(request.get('Model') or row.get('Model'), True)
        vendor_key(row, fixture)
    elif request['Action'] == 'codex-save':
        identity = codex_identity(read(codex_home(fixture) / 'auth.json'))
        if not identity['SignedIn']:
            raise ValueError('Codex 当前没有 ChatGPT 账号登录，请先在 Codex 中登录后再收录。')
        if request.get('ExpectedAccount') and request['ExpectedAccount'] != identity['Account']:
            raise ValueError('Codex 当前登录的账号与所选卡片不一致，请刷新后重试。')


def execute(request, data_root, fixture=None, db=None):
    if request.get('MaintenanceConfirmed') is not True:
        raise ValueError('请先确认已退出 Claude，再执行操作。')
    action = ACTIONS.get(request.get('Action'))
    if action is None:
        raise ValueError('未知的本地操作。')
    app = 'codex' if request['Action'].startswith('codex-') else 'claude'
    label = APPS[app]['Label'].split()[0]
    if request['Action'] in CONFIG_ACTIONS:
        # MCP and skill toggles edit user configuration only; apps pick them up when they restart.
        with auth_operation(app_paths(fixture)[2]):
            return action(request, data_root, fixture, db)
    quit_requested, relaunch = request.get('QuitClaude') is True, request.get('RelaunchClaude') is True
    if not quit_requested:
        ensure_closed(fixture, app)
    profiles = app_paths(fixture)[2]
    with auth_operation(profiles):
        reject_pending(data_root, profiles)
        preflight(request, fixture, db, data_root)
        target = quit_desktop(fixture, app=app) if quit_requested else None
        if relaunch and target is None:
            target = launch_target((), fixture, app)
        try:
            result = action(request, data_root, fixture, db)
        except Exception as error:
            if not (relaunch and target):
                raise
            # Reopen only when every journal is final, i.e. the previous login was left or restored intact.
            try:
                reject_pending(data_root, profiles)
                reopened = launch_desktop(target, fixture, app)
            except Exception:
                reopened = False
            note = f' 已按原登录状态重新打开 {label}。' if reopened else f' {label} 未自动重新打开，请先核对时间戳备份。'
            raise ValueError(str(error) + note) from error
        if app == 'claude':
            try:
                record_timeline(data_root, fixture)
            except Exception:
                pass  # Statistics only; never fail a completed login change over it.
        if app == 'claude' and request.get('SyncConversations') is True and request['Action'] != 'sync-conversations':
            # Claude is still closed here, so Desktop cannot rewrite a list while it is extended.
            try:
                synced = sync_conversations(data_root, fixture)
                if synced.get('Journal'):
                    result['Message'] += ' ' + synced['Message']
            except Exception as error:
                result['Message'] += f' 对话列表未同步：{error}'
    if relaunch:
        result['Relaunched'] = launch_desktop(target, fixture, app)
        if result['Relaunched']:
            # The actions' own messages assume the user reopens the app; say what actually happened.
            message = result['Message'].replace('请自行打开 Claude 确认', '请在 Claude 中确认').replace('请自行打开 Claude。', '')
            result['Message'] = message.rstrip() + f' 已自动重新打开 {label}。'
        else:
            result['Message'] += f' 未找到 {label} 启动入口，请手动打开。'
    return result


if __name__ == '__main__':
    main()
