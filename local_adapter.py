"""Local identity/provider adapter. No process is ever killed by this module.

Catalog operations are read-only. Mutations require an explicit maintenance confirmation,
closed Claude processes, and a timestamped, verified preimage backup.
"""
from __future__ import annotations
import argparse
from contextlib import closing, contextmanager
import hashlib
import hmac
import http.server
import json
import os
from pathlib import Path
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


def ensure_closed(fixture=None):
    if fixture:
        return
    output = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                             'if (Get-Process -Name claude -ErrorAction SilentlyContinue) { exit 10 }'],
                            creationflags=NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if output.returncode:
        raise ValueError('请先退出 Claude Desktop、第三方版及 Claude Code。工具不会强制关闭正在工作的会话。')


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


def save_account(request, data_root, fixture=None):
    ensure_closed(fixture)
    official, _, profiles, user = app_paths(fixture)
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


def switch_account(request, data_root, fixture=None):
    ensure_closed(fixture)
    official, third, profiles, _ = app_paths(fixture)
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


def get_provider(identifier, db=None):
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
    row = get_provider(str(request.get('ProviderId', '')), db)
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
    return {'Message': f'已选用 {row["name"]}。请自行打开 Claude。原文、记忆及账号凭据均未搬动。', 'Backup': str(backup)}


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
    parser.add_argument('action', choices=('catalog', 'execute', 'relay'))
    parser.add_argument('--request'); parser.add_argument('--response'); parser.add_argument('--data-root')
    parser.add_argument('--fixture'); parser.add_argument('--database')
    args = parser.parse_args()
    if args.action == 'catalog':
        print(json.dumps(catalog(args.database, args.fixture, args.data_root), ensure_ascii=False)); return
    if args.action == 'relay':
        config = read(Path(args.data_root) / 'relay.json')
        server = http.server.ThreadingHTTPServer(('127.0.0.1', int(config['Port'])), relay_handler(args.data_root))
        try: server.serve_forever()
        finally: server.server_close()
        return
    try:
        request = read(args.request)
        if request.get('MaintenanceConfirmed') is not True:
            raise ValueError('请先确认已退出 Claude，再执行操作。')
        data_root = Path(args.data_root)
        ensure_closed(args.fixture)
        with auth_operation(app_paths(args.fixture)[2]):
            reject_pending(data_root, app_paths(args.fixture)[2])
            if request['Action'] == 'save':
                result = save_account(request, data_root, args.fixture)
            elif request['Action'] == 'switch':
                result = switch_account(request, data_root, args.fixture)
            elif request['Action'] == 'provider-switch':
                result = switch_provider(request, data_root, args.fixture, args.database)
            else:
                raise ValueError('未知的本地操作。')
        response = {'Ok': True, 'Result': result}
    except Exception as error:
        # No upstream tokens or response bodies are interpolated into adapter exceptions.
        response = {'Ok': False, 'Error': str(error)}
    atomic_json(args.response, response)


if __name__ == '__main__':
    main()
