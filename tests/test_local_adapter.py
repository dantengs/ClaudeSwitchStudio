import importlib.util
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import http.server
import threading
import os
import urllib.request
import urllib.error
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('adapter', Path(__file__).resolve().parents[1] / 'local_adapter.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp_parent = Path(__file__).resolve().parent / '.tmp'
        self.temp_parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='claude-studio-fixture-', dir=self.temp_parent)
        self.root = Path(self.temp.name)
        self.official, self.third, self.profiles, self.user = adapter.app_paths(self.root)
        self.data = self.root / 'data'
        for item in adapter.AUTH_ITEMS:
            target = self.official / item
            if item in ('Local State', 'config.json'):
                adapter.atomic_json(target, {'lastKnownAccountUuid': '11111111-1111-4111-8111-111111111111', 'oauth:tokenCacheV2': 'encrypted-fixture-A'} if item == 'config.json' else {'dummy': 'encrypted-fixture'})
            else:
                target.mkdir(parents=True)
                (target / 'sample').write_text('fixture', encoding='utf-8')
        adapter.atomic_json(self.user / '.claude.json', {'oauthAccount': {'accountUuid': '11111111-1111-4111-8111-111111111111', 'emailAddress': 'fixture@example.test'}})
        self.db = self.root / 'providers.db'
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute('create table providers(id text,name text,settings_config text,meta text,app_type text)')
            connection.execute('insert into providers values(?,?,?,?,?)', ('kimi-fixture', 'Kimi 测试', json.dumps({'env': {'ANTHROPIC_BASE_URL': 'https://example.test/coding', 'ANTHROPIC_AUTH_TOKEN': 'secret-fixture-not-for-ui'}}), json.dumps({'claudeDesktopMode': 'direct'}), 'claude-desktop'))
            connection.commit()

    def tearDown(self):
        # The only recursive cleanup target is the TemporaryDirectory created by this test.
        self.assertEqual(self.root.resolve().parent, self.temp_parent.resolve())
        self.assertTrue(self.root.name.startswith('claude-studio-fixture-'))
        self.temp.cleanup()

    def test_catalog_excludes_credentials(self):
        result = adapter.catalog(self.db, self.root)
        self.assertEqual(len(result['Providers']), 1)
        self.assertNotIn('secret-fixture-not-for-ui', json.dumps(result))
        self.assertNotIn('ANTHROPIC_AUTH_TOKEN', json.dumps(result))

    def test_save_and_switch_uses_timestamp_backups(self):
        saved = adapter.save_account({'Name': '账号A'}, self.data, self.root)
        snapshot = Path(saved['Backup'])
        self.assertTrue(adapter.read(snapshot / 'manifest.json')['Verified'])
        self.assertEqual(adapter.read(self.profiles / saved['Profile'] / 'studio-profile.json')['Email'], 'fixture@example.test')
        adapter.atomic_json(self.official / 'config.json', {'lastKnownAccountUuid': '22222222-2222-4222-8222-222222222222'})
        adapter.atomic_json(self.third / 'configLibrary/_meta.json', {'entries': [{'id': adapter.PROFILE_ID}, {'id': 'user-owned-profile'}], 'appliedId': adapter.PROFILE_ID})
        adapter.atomic_json(self.third / 'configLibrary' / (adapter.PROFILE_ID + '.json'), {'inferenceProvider': 'gateway', 'inferenceGatewayApiKey': 'fixture-secret', 'userSetting': 'keep'})
        switched = adapter.switch_account({'Name': saved['Profile']}, self.data, self.root)
        self.assertNotEqual(switched['Backup'], saved['Backup'])
        self.assertEqual(adapter.read(self.official / 'config.json')['lastKnownAccountUuid'], '11111111-1111-4111-8111-111111111111')
        self.assertTrue(snapshot.exists())
        self.assertEqual(adapter.read(self.third / 'configLibrary/_meta.json')['entries'], [{'id': 'user-owned-profile'}])
        self.assertEqual(adapter.read(self.third / 'configLibrary' / (adapter.PROFILE_ID + '.json')), {'userSetting': 'keep'})

    def test_provider_switch_keeps_other_settings(self):
        adapter.atomic_json(self.official / 'claude_desktop_config.json', {'userSetting': 'keep'})
        result = adapter.switch_provider({'ProviderId': 'kimi-fixture'}, self.data, self.root, self.db)
        self.assertTrue(Path(result['Backup']).exists())
        outgoing = adapter.account_profile(self.profiles, '11111111-1111-4111-8111-111111111111')
        self.assertEqual(adapter.read(outgoing / 'config.json')['oauth:tokenCacheV2'], 'encrypted-fixture-A')
        self.assertEqual(adapter.read(self.official / 'claude_desktop_config.json'), {'deploymentMode': '3p', 'userSetting': 'keep'})
        profile = adapter.read(self.third / 'configLibrary' / (adapter.PROFILE_ID + '.json'))
        self.assertEqual(profile['inferenceGatewayBaseUrl'], 'https://example.test/coding')

    def test_unwritten_files_not_overwritten_on_failure(self):
        first, second = self.root / 'first.json', self.root / 'second.json'
        adapter.atomic_json(first, {'old': 1}); adapter.atomic_json(second, {'old': 2})
        real_atomic = adapter.atomic_json
        def fail_second(path, value):
            if Path(path) == second and value == {'new': 2}:
                # Simulate an unrelated actor changing a file that this operation never wrote.
                real_atomic(second, {'external': True})
                raise OSError('injected failure')
            return real_atomic(path, value)
        with patch.object(adapter, 'atomic_json', fail_second):
            with self.assertRaises(OSError):
                adapter.patch_files([(first, {'new': 1}), (second, {'new': 2})], self.data, self.root)
        self.assertEqual(adapter.read(first), {'old': 1})
        self.assertEqual(adapter.read(second), {'external': True})

    def test_relay_start_failure_rolls_configuration_back(self):
        first=self.root/'config-before.json'
        adapter.atomic_json(first, {'preserved': True})
        def failure(): raise RuntimeError('simulated port conflict')
        with self.assertRaises(RuntimeError):
            adapter.patch_files([(first, {'changed': True})], self.data, self.root, ready_callback=failure)
        self.assertEqual(adapter.read(first), {'preserved': True})

    @unittest.skipUnless(os.name == 'nt', 'Windows directory junction test')
    def test_windows_junctions_share_one_directory(self):
        pool=self.root/'junction-pool';pool.mkdir()
        first=self.root/'account-a';second=self.root/'account-b'
        env=dict(os.environ, STUDIO_LAB_POOL=str(pool), STUDIO_LAB_A=str(first), STUDIO_LAB_B=str(second))
        command='$ErrorActionPreference="Stop";New-Item -ItemType Junction -Path $env:STUDIO_LAB_A -Target $env:STUDIO_LAB_POOL | Out-Null;New-Item -ItemType Junction -Path $env:STUDIO_LAB_B -Target $env:STUDIO_LAB_POOL | Out-Null'
        result=adapter.subprocess.run(['powershell.exe','-NoProfile','-Command',command],env=env,creationflags=adapter.NO_WINDOW,capture_output=True)
        self.assertEqual(result.returncode,0)
        try:
            (first/'memory.md').write_text('one copy',encoding='utf-8')
            self.assertEqual((second/'memory.md').read_text(encoding='utf-8'),'one copy')
            (second/'memory.md').write_text('updated',encoding='utf-8')
            self.assertEqual((pool/'memory.md').read_text(encoding='utf-8'),'updated')
            self.assertTrue(os.path.samefile(first/'memory.md',second/'memory.md'))
        finally:
            for link in (first,second):
                self.assertEqual(link.parent.resolve(),self.root.resolve())
                self.assertEqual(link.resolve(),pool.resolve())
                os.rmdir(link)  # Remove the link itself, never recurse into the shared target.
        self.assertEqual((pool/'memory.md').read_text(encoding='utf-8'),'updated')

    def test_running_claude_refuses_before_backup(self):
        with patch.object(adapter.subprocess, 'run') as run:
            run.return_value.returncode = 10
            with self.assertRaises(ValueError):
                adapter.ensure_closed()
        self.assertFalse(self.data.exists())

    # Synthetic process tables only; these tests never list, close or start a real process.
    A_ID = '11111111-1111-4111-8111-111111111111'
    B_ID = '22222222-2222-4222-8222-222222222222'
    DESKTOP = 'C:\\Program Files\\WindowsApps\\Claude_9.9.0.0_x64__abcdefghijk12\\app\\Claude.exe'

    def running(self, *extra):
        rows = [{'Pid': 100, 'ParentPid': 4, 'Path': self.DESKTOP}, {'Pid': 101, 'ParentPid': 100, 'Path': self.DESKTOP},
                {'Pid': 102, 'ParentPid': 100, 'Path': 'C:\\Users\\fixture\\AppData\\Local\\Claude-Data\\x\\claude-code\\1.0\\claude.exe'}]
        adapter.atomic_json(self.root / 'processes.json', rows + list(extra))

    def process_log(self):
        return adapter.read(self.root / 'process-log.json', [])

    def two_accounts(self):
        a = adapter.save_account({}, self.data, self.root)
        adapter.atomic_json(self.official / 'config.json', {'lastKnownAccountUuid': self.B_ID, 'oauth:tokenCacheV2': 'encrypted-fixture-B', 'locale': 'zh-CN'})
        return a

    def request(self, action, **values):
        return dict({'Action': action, 'MaintenanceConfirmed': True}, **values)

    def test_process_classification_separates_desktop_agents_and_independent_cli(self):
        rows = [{'Pid': 100, 'ParentPid': 4, 'Path': self.DESKTOP}, {'Pid': 101, 'ParentPid': 100, 'Path': self.DESKTOP},
                {'Pid': 102, 'ParentPid': 100, 'Path': 'C:\\Users\\fixture\\AppData\\Local\\Claude-Data\\x\\claude-code\\1.0\\claude.exe'},
                # The same bundled binary started from a terminal is an independent session.
                {'Pid': 103, 'ParentPid': 7, 'Path': 'C:\\Users\\fixture\\AppData\\Local\\Claude-Data\\x\\claude-code\\1.0\\claude.exe'},
                {'Pid': 104, 'ParentPid': 7, 'Path': 'C:\\Users\\fixture\\.local\\bin\\claude.exe'},
                {'Pid': 105, 'ParentPid': 7, 'Path': None}]
        with patch.object(adapter, 'powershell', return_value=json.dumps(rows)), patch.object(adapter, 'installed_app_id', return_value=''):
            kinds = {r['Pid']: r['Kind'] for r in adapter.claude_processes()}
            self.assertEqual(kinds, {100: 'desktop', 101: 'desktop', 102: 'agent', 103: 'cli', 104: 'cli', 105: 'unknown'})
            summary = adapter.process_summary()
            self.assertEqual((summary['Desktop'], summary['Agents'], summary['Cli'], summary['CanQuit']), (2, 1, 2, False))
            self.assertEqual(adapter.launch_target(adapter.claude_processes()), {'Kind': 'appx', 'Id': 'Claude_abcdefghijk12!Claude'})
        with patch.object(adapter, 'powershell', return_value=json.dumps(rows[-1])):
            with self.assertRaisesRegex(ValueError, '无法识别'):
                adapter.quit_desktop()

    def test_running_desktop_blocks_without_quit_but_independent_cli_does_not(self):
        a = self.two_accounts()
        self.running()
        before = sorted(p.name for p in (self.data / 'private-auth-backups').iterdir())
        with self.assertRaisesRegex(ValueError, '仍在运行'):
            adapter.execute(self.request('switch', Name=a['Profile']), self.data, self.root)
        self.assertEqual(sorted(p.name for p in (self.data / 'private-auth-backups').iterdir()), before)
        self.assertEqual(self.process_log(), [])
        adapter.atomic_json(self.root / 'processes.json', [{'Pid': 7, 'ParentPid': 1, 'Path': 'C:\\Users\\fixture\\.local\\bin\\claude.exe'}])
        adapter.execute(self.request('switch', Name=a['Profile']), self.data, self.root)
        self.assertEqual(adapter.read(self.official / 'config.json')['lastKnownAccountUuid'], self.A_ID)

    def test_confirmed_switch_quits_desktop_switches_and_reopens(self):
        a = self.two_accounts()
        cli = {'Pid': 7, 'ParentPid': 1, 'Path': 'C:\\Users\\fixture\\.local\\bin\\claude.exe'}
        self.running(cli)
        result = adapter.execute(self.request('switch', Name=a['Profile'], ExpectedAccount=self.A_ID, QuitClaude=True, RelaunchClaude=True), self.data, self.root)
        self.assertEqual(adapter.read(self.official / 'config.json')['lastKnownAccountUuid'], self.A_ID)
        self.assertEqual(self.process_log(), [{'Quit': [100, 101, 102]}, {'Launch': {'Kind': 'appx', 'Id': 'Claude_abcdefghijk12!Claude'}}])
        self.assertEqual(adapter.read(self.root / 'processes.json'), [dict(cli, Kind='cli')])
        self.assertTrue(result['Relaunched'])
        self.assertNotIn('自行打开', result['Message'])
        timeline = adapter.read(self.data / 'account-timeline.json')
        self.assertEqual((timeline[-1]['Mode'], timeline[-1]['Account']), ('1p', self.A_ID))
        outgoing = adapter.account_profile(self.profiles, self.B_ID)
        self.assertEqual(adapter.read(outgoing / 'config.json')['oauth:tokenCacheV2'], 'encrypted-fixture-B')

    def test_invalid_request_is_refused_before_desktop_is_closed(self):
        a = self.two_accounts()
        self.running()
        with self.assertRaisesRegex(ValueError, '不一致'):
            adapter.execute(self.request('switch', Name=a['Profile'], ExpectedAccount='wrong', QuitClaude=True, RelaunchClaude=True), self.data, self.root)
        self.assertEqual(self.process_log(), [])
        adapter.atomic_json(self.official / 'claude_desktop_config.json', {'deploymentMode': '3p'})
        with self.assertRaisesRegex(ValueError, '第三方模式'):
            adapter.execute(self.request('login-new', QuitClaude=True, RelaunchClaude=True), self.data, self.root)
        self.assertEqual(self.process_log(), [])

    def test_failure_before_changes_reopens_desktop_but_review_state_does_not(self):
        a = self.two_accounts()
        self.running()
        with patch.object(adapter, 'snapshot', side_effect=OSError('fixture backup failure')):
            with self.assertRaisesRegex(ValueError, '已按原登录状态重新打开'):
                adapter.execute(self.request('switch', Name=a['Profile'], QuitClaude=True, RelaunchClaude=True), self.data, self.root)
        self.assertEqual(adapter.read(self.official / 'config.json')['lastKnownAccountUuid'], self.B_ID)
        self.assertEqual([list(entry) for entry in self.process_log()], [['Quit'], ['Launch']])
        self.running()
        (self.root / 'process-log.json').unlink()
        original = adapter.replace_within
        def fail_install(source, destination, allowed_root):
            if Path(destination).parent.name.startswith('.studio-displaced-') and Path(source).name == 'Session Storage':
                raise OSError('fixture failure while installing target login')
            return original(source, destination, allowed_root)
        with patch.object(adapter, 'replace_within', fail_install):
            with self.assertRaisesRegex(ValueError, '未自动重新打开'):
                adapter.execute(self.request('switch', Name=a['Profile'], QuitClaude=True, RelaunchClaude=True), self.data, self.root)
        # A journal awaiting review keeps Claude closed so nothing writes over the files to be checked.
        self.assertEqual([list(entry) for entry in self.process_log()], [['Quit']])
        self.assertEqual(adapter.read(self.official / 'config.json')['lastKnownAccountUuid'], self.B_ID)

    def session(self, account, org, sid, cli, activity=1, **extra):
        path = self.official / 'claude-code-sessions' / account / org / f'local_{sid}.json'
        adapter.atomic_json(path, dict({'sessionId': f'local_{sid}', 'cliSessionId': cli, 'title': sid[:4], 'lastActivityAt': activity}, **extra))
        return path

    def test_conversation_lists_are_shared_both_ways_without_overwriting(self):
        org_a, org_b = 'aaaaaaaa-0000-4000-8000-000000000001', 'bbbbbbbb-0000-4000-8000-000000000002'
        x, y, w, gone = ('10000000-0000-4000-8000-00000000000' + c for c in '1234')
        for cli in ('cli-x', 'cli-y', 'cli-w'):
            (self.user / '.claude/projects/p').mkdir(parents=True, exist_ok=True)
            (self.user / '.claude/projects/p' / f'{cli}.jsonl').write_text('{}', encoding='utf-8')
        self.session(self.A_ID, org_a, x, 'cli-x', bridgeSessionIds=['bridge-of-A'], scheduledTaskId='task-A', remoteMcpServersConfig=[{'uuid': 'connector-A'}])
        self.session(self.A_ID, org_a, w, 'cli-w')
        self.session(self.A_ID, org_a, gone, 'cli-without-transcript')
        own_y = self.session(self.B_ID, org_b, y, 'cli-y', activity=5)
        adapter.atomic_json(self.official / 'claude-code-sessions' / self.B_ID / org_a / 'scheduled-tasks.json', [])
        (own_y.parent / f'deleted_{w}').write_text('', encoding='utf-8')  # B deleted w; never bring it back.
        stale_y = self.session(self.A_ID, org_a, y, 'cli-y', activity=1, title='renamed-in-A', titleSource='user', previousTitles=['1000'])
        result = adapter.sync_conversations(self.data, self.root)
        b_org = own_y.parent
        self.assertTrue((b_org / f'local_{x}.json').exists())
        self.assertFalse((b_org / f'local_{w}.json').exists())
        self.assertFalse((b_org / f'local_{gone}.json').exists())
        # The organization folder B does not use stays empty.
        self.assertFalse((self.official / 'claude-code-sessions' / self.B_ID / org_a / f'local_{x}.json').exists())
        # Both copies of y now share A's manual title and B's later progress; originals are kept aside.
        self.assertEqual(adapter.read(own_y)['title'], 'renamed-in-A')
        self.assertEqual(adapter.read(stale_y)['lastActivityAt'], 5)
        copied = adapter.read(b_org / f'local_{x}.json')
        self.assertEqual(copied['bridgeSessionIds'], [])
        self.assertNotIn('scheduledTaskId', copied); self.assertNotIn('remoteMcpServersConfig', copied)
        self.assertEqual((result['Created'], result['Updated']), (1, 2))
        journal = adapter.read(result['Journal'])
        self.assertEqual(journal['Status'], 'committed')
        # Verified, so this sync's own backup folder is gone; nothing else under the data folder is touched.
        self.assertTrue(journal['Verified'] and journal['BackupRemoved'])
        self.assertFalse(Path(journal['Updated'][0]['Backup']).exists())
        self.assertEqual(list((self.data / 'conversation-sync-originals').iterdir()), [])
        again = adapter.sync_conversations(self.data, self.root)
        self.assertEqual((again['Created'], again['Updated']), (0, 0))
        self.assertNotIn('Journal', again)

    def test_renamed_and_moved_conversations_follow_the_latest_edit(self):
        org_a, org_b = 'aaaaaaaa-0000-4000-8000-000000000001', 'bbbbbbbb-0000-4000-8000-000000000002'
        sid = '30000000-0000-4000-8000-000000000001'
        (self.user / '.claude/projects/p').mkdir(parents=True)
        (self.user / '.claude/projects/p/cli-z.jsonl').write_text('{}', encoding='utf-8')
        in_a = self.session(self.A_ID, org_a, sid, 'cli-z', title='old', titleSource='user', previousTitles=['x', 'y', 'z'], cwd='C:\\A')
        in_b = self.session(self.B_ID, org_b, sid, 'cli-z', title='old', titleSource='user', previousTitles=['x', 'y', 'z'], cwd='C:\\A')
        self.assertNotIn('Journal', adapter.sync_conversations(self.data, self.root))  # Records the shared baseline.
        # B renames once (fewer previous titles than A has); A moves the conversation to another folder.
        adapter.atomic_json(in_b, dict(adapter.read(in_b), title='new', previousTitles=['old']))
        adapter.atomic_json(in_a, dict(adapter.read(in_a), cwd='C:\\B', originCwd='C:\\B'))
        result = adapter.sync_conversations(self.data, self.root)
        self.assertEqual(result['Updated'], 2)
        for path in (in_a, in_b):
            value = adapter.read(path)
            self.assertEqual((value['title'], value['previousTitles'], value['cwd'], value['originCwd']), ('new', ['old'], 'C:\\B', 'C:\\B'))
        self.assertIn('统一了 2 个对话条目', result['Message'])

    def ldb_store(self, folder, account, scope, values, state=None):
        """A minimal LevelDB store as Chromium leaves it: manifest, empty log, then one appended batch."""
        folder.mkdir(parents=True)
        edit = b'\x01' + adapter.put_varint(26) + b'leveldb.BytewiseComparator' + b'\x02\x03\x03\x04\x04\x00'
        (folder / 'MANIFEST-000001').write_bytes(adapter.masked_crc(b'\x01' + edit).to_bytes(4, 'little') + len(edit).to_bytes(2, 'little') + b'\x01' + edit)
        (folder / 'CURRENT').write_text('MANIFEST-000001\n', encoding='ascii')
        (folder / '000003.log').write_bytes(b'')
        frame = {'state': dict(state or {}, lastSidebarScopeKey=scope), 'version': 1, 'shared': 1}
        items = [(adapter.ls_key('dframe-store'), adapter.ls_value(json.dumps(frame, ensure_ascii=False)))]
        items += [(adapter.ls_key(k), adapter.ls_value(json.dumps(v, ensure_ascii=False))) for k, v in values.items()]
        adapter.ldb_put(folder, items, 0)
        adapter.atomic_json(folder.parent.parent / 'config.json', {'lastKnownAccountUuid': account})

    def desktop_folders(self, folder, account, scope, groups, sections, moment):
        """Desktop's layout: folders in the dframe-store state, mirrored to LSS keys with their change time."""
        mirrors = {adapter.SIDEBAR_KEYS[0]: {'value': groups, 'tabId': 't', 'timestamp': moment},
                   adapter.SIDEBAR_KEYS[1]: {'value': sections, 'tabId': 't', 'timestamp': moment}}
        self.ldb_store(folder, account, scope, mirrors, {'customGroupsByScope': groups, 'codeSidebarByScope': sections,
                                                         'interfaceFont': '思源黑体', 'collapsedGroups': ['project-C:\\p']})

    def frame(self, folder):
        return json.loads(adapter.ls_text(adapter.ldb_state(folder)[0][adapter.ls_key('dframe-store')]))

    def mirror(self, folder, index):
        return json.loads(adapter.ls_text(adapter.ldb_state(folder)[0][adapter.ls_key(adapter.SIDEBAR_KEYS[index])]))

    def rewrite_frame(self, folder, change, moment):
        """What Desktop does when folders change: update the state and rebuild both mirrors."""
        values, last = adapter.ldb_state(folder)
        frame = self.frame(folder)
        frame['state'].update(change)
        items = [(adapter.ls_key('dframe-store'), adapter.ls_value(json.dumps(frame, ensure_ascii=False)))]
        for index, (field, _) in enumerate(adapter.SIDEBAR_FIELDS):
            items.append((adapter.ls_key(adapter.SIDEBAR_KEYS[index]), adapter.ls_value(json.dumps(
                {'value': frame['state'][field], 'tabId': 't', 'timestamp': moment}, ensure_ascii=False))))
        adapter.ldb_put(folder, items, last)

    def test_sidebar_folders_follow_the_account_edited_last(self):
        org_a, org_b = 'aaaaaaaa-0000-4000-8000-000000000001', 'bbbbbbbb-0000-4000-8000-000000000002'
        scope_a, scope_b = f'{self.A_ID}/{org_a}', f'{self.B_ID}/{org_b}'
        sid = '40000000-0000-4000-8000-000000000001'
        mine, other = f'code:local_{sid}', 'code:local_50000000-0000-4000-8000-000000000001'
        (self.user / '.claude/projects/p').mkdir(parents=True)
        (self.user / '.claude/projects/p/cli-f.jsonl').write_text('{"fixture": "transcript"}', encoding='utf-8')
        self.session(self.A_ID, org_a, sid, 'cli-f', title='same', cwd='C:\\p')
        self.session(self.B_ID, org_b, sid, 'cli-f', title='same', cwd='C:\\p')
        empty = {'groups': [], 'assignments': {}, 'order': {}}
        groups_a = {'groups': [{'id': 'cg-a', 'name': 'A folder'}], 'assignments': {mine: 'cg-a', other: 'cg-a'}, 'order': {'cg-a': [mine, other]}}
        groups_b = {'groups': [{'id': 'cg-1', 'name': '研究'}], 'assignments': {mine: 'cg-1'}, 'order': {'cg-1': [mine]}}
        plain = {'sections': [{'id': 'sessions', 'kind': 'sessions'}], 'prefsSeeded': True}
        sections_b = {'sections': [{'id': 'cg-1', 'kind': 'manual', 'name': '研究', 'members': []}, {'id': 'sessions', 'kind': 'sessions'}], 'prefsSeeded': True}
        live = self.official / 'Local Storage/leveldb'
        (self.official / 'Local Storage/sample').unlink(); (self.official / 'Local Storage').rmdir()
        self.desktop_folders(live, self.A_ID, scope_a, {scope_a: groups_a}, {scope_a: plain}, 100)
        saved_b = self.profiles / 'desktop-B' / 'Local Storage/leveldb'
        self.desktop_folders(saved_b, self.B_ID, scope_b, {scope_b: groups_b, scope_a: empty}, {scope_b: sections_b, scope_a: plain}, 200)
        conversations = {p: p.read_bytes() for p in list((self.official / 'claude-code-sessions').rglob('*')) + list(self.user.rglob('*.jsonl')) if p.is_file()}
        before = adapter.ldb_state(saved_b)
        result = adapter.sync_conversations(self.data, self.root)
        self.assertTrue(result['Folders'])
        # Conversation indexes and transcripts are never touched by the folder step.
        self.assertEqual({p: p.read_bytes() for p in conversations}, conversations)
        self.assertEqual(result['Updated'], 0)
        # First sync: A (the first account) leads; B adds its own folder, but the conversation both list stays in A's.
        merged = {'groups': [{'id': 'cg-a', 'name': 'A folder'}, {'id': 'cg-1', 'name': '研究'}],
                  'assignments': {mine: 'cg-a', other: 'cg-a'}, 'order': {'cg-a': [mine, other], 'cg-1': []}}
        state = self.frame(live)['state']
        self.assertEqual(state['customGroupsByScope'], {scope_a: merged, scope_b: merged})
        sections = {'sections': plain['sections'] + [sections_b['sections'][0]], 'prefsSeeded': True}
        self.assertEqual(state['codeSidebarByScope'], {scope_a: sections, scope_b: sections})
        self.assertEqual(adapter.read(self.data / 'conversation-sync-state.json')['PrimaryAccount'], self.A_ID)
        self.assertEqual((state['interfaceFont'], state['collapsedGroups'], state['lastSidebarScopeKey']), ('思源黑体', ['project-C:\\p'], scope_a))
        self.assertEqual(self.mirror(live, 0)['value'], state['customGroupsByScope'])  # Mirrors agree with the state.
        self.assertEqual(self.mirror(live, 1)['value'], state['codeSidebarByScope'])
        self.assertEqual(adapter.ldb_state(saved_b), before)  # Saved logins are only read.
        journal = adapter.read(result['Journal'])
        self.assertTrue(journal['Verified'] and journal['BackupRemoved'])
        self.assertFalse(Path(journal['Sidebar']['Backup']).exists())
        # Desktop is asked to merge the server copy in and upload, instead of letting the server copy win.
        pending = adapter.ldb_state(live)[0][adapter.ls_key(adapter.SYNC_PENDING_KEY)]
        self.assertEqual(adapter.ls_text(pending), '1|migrate')
        # Desktop starting up rebuilds the mirrors from the same state: nothing to do.
        self.rewrite_frame(live, {}, self.mirror(live, 0)['timestamp'] + 2500)
        self.assertFalse(adapter.sync_conversations(self.data, self.root)['Folders'])
        # Minutes later, A deletes its own folder and files the conversation under 研究; B's stale copy must not undo it.
        kept = {'groups': [{'id': 'cg-1', 'name': '研究'}], 'assignments': {mine: 'cg-1'}, 'order': {'cg-1': [mine]}}
        self.rewrite_frame(live, {'customGroupsByScope': dict(state['customGroupsByScope'], **{scope_a: kept})},
                           self.mirror(live, 0)['timestamp'] + 180_000)
        self.assertTrue(adapter.sync_conversations(self.data, self.root)['Folders'])
        self.assertEqual(self.frame(live)['state']['customGroupsByScope'], {scope_a: kept, scope_b: kept})
        self.assertEqual({p: p.read_bytes() for p in conversations}, conversations)

    def test_old_mirror_only_baseline_is_ignored(self):
        """0.4.1 stored a baseline of mirrors Desktop then rebuilt; it must not read as a deletion."""
        org_a = 'aaaaaaaa-0000-4000-8000-000000000001'
        scope_a, scope_b = f'{self.A_ID}/{org_a}', f'{self.B_ID}/bbbbbbbb-0000-4000-8000-000000000002'
        (self.user / '.claude/projects/p').mkdir(parents=True)
        (self.user / '.claude/projects/p/cli-g.jsonl').write_text('{}', encoding='utf-8')
        self.session(self.A_ID, org_a, '60000000-0000-4000-8000-000000000001', 'cli-g')
        self.session(self.B_ID, scope_b.split('/')[1], '60000000-0000-4000-8000-000000000001', 'cli-g')
        folder_b = {'groups': [{'id': 'cg-u', 'name': 'Useful Tools'}], 'assignments': {'code:local_x': 'cg-u'}, 'order': {}}
        empty = {'groups': [], 'assignments': {}, 'order': {}}
        live = self.official / 'Local Storage/leveldb'
        (self.official / 'Local Storage/sample').unlink(); (self.official / 'Local Storage').rmdir()
        self.desktop_folders(live, self.A_ID, scope_a, {scope_a: empty, scope_b: folder_b}, {}, 5000)
        adapter.atomic_json(self.data / 'conversation-sync-state.json', {'Sidebar': {adapter.SIDEBAR_KEYS[0]: {'Layout': folder_b, 'Moment': 10}}})
        adapter.sync_conversations(self.data, self.root)
        self.assertEqual(self.frame(live)['state']['customGroupsByScope'], {scope_a: folder_b, scope_b: folder_b})

    def two_scopes_with_a_folder(self):
        """A and B share one conversation; A (the live account) files it in "Useful Tools"."""
        org_a, org_b = 'aaaaaaaa-0000-4000-8000-000000000001', 'bbbbbbbb-0000-4000-8000-000000000002'
        scope_a, scope_b = f'{self.A_ID}/{org_a}', f'{self.B_ID}/{org_b}'
        sid = '80000000-0000-4000-8000-000000000001'
        (self.user / '.claude/projects/p').mkdir(parents=True)
        (self.user / '.claude/projects/p/cli-h.jsonl').write_text('{"keep": true}', encoding='utf-8')
        self.session(self.A_ID, org_a, sid, 'cli-h', title='A title', titleSource='user', previousTitles=['x'])
        self.session(self.B_ID, org_b, sid, 'cli-h', title='B title')
        folder = {'groups': [{'id': 'cg-u', 'name': 'Useful Tools'}], 'assignments': {f'code:local_{sid}': 'cg-u'}, 'order': {'cg-u': [f'code:local_{sid}']}}
        empty = {'groups': [], 'assignments': {}, 'order': {}}
        live = self.official / 'Local Storage/leveldb'
        (self.official / 'Local Storage/sample').unlink(); (self.official / 'Local Storage').rmdir()
        self.desktop_folders(live, self.A_ID, scope_a, {scope_a: folder, scope_b: empty}, {}, 100)
        return live, scope_a, scope_b, folder, empty

    def test_server_copy_wiping_folders_right_after_a_sync_does_not_spread(self):
        live, scope_a, scope_b, folder, empty = self.two_scopes_with_a_folder()
        adapter.sync_conversations(self.data, self.root)
        self.assertEqual(self.frame(live)['state']['customGroupsByScope'], {scope_a: folder, scope_b: folder})
        # Seconds later Desktop applies B's stale server copy to B's scope (what 0.4.2 ran into).
        wiped = dict(self.frame(live)['state']['customGroupsByScope'], **{scope_b: empty})
        self.rewrite_frame(live, {'customGroupsByScope': wiped}, self.mirror(live, 0)['timestamp'] + 4000)
        adapter.sync_conversations(self.data, self.root)
        self.assertEqual(self.frame(live)['state']['customGroupsByScope'], {scope_a: folder, scope_b: folder})
        self.assertEqual(adapter.ls_text(adapter.ldb_state(live)[0][adapter.ls_key(adapter.SYNC_PENDING_KEY)]), '1|migrate')

    def test_0_4_2_baseline_is_replaced_by_a_fresh_merge(self):
        live, scope_a, scope_b, folder, empty = self.two_scopes_with_a_folder()
        # 0.4.2 recorded the folder, then the server copy wiped A's scope; that must not read as a deletion.
        adapter.atomic_json(self.data / 'conversation-sync-state.json', {'PrimaryAccount': self.A_ID, 'Folders': {
            'customGroupsByScope': {'Layout': folder, 'Moment': 1, 'Scopes': [scope_a, scope_b]}}})
        self.rewrite_frame(live, {'customGroupsByScope': {scope_a: folder, scope_b: empty}}, 10_000)
        adapter.sync_conversations(self.data, self.root)
        self.assertEqual(self.frame(live)['state']['customGroupsByScope'], {scope_a: folder, scope_b: folder})

    def test_no_upload_mark_in_a_store_another_account_owns(self):
        live, *_ = self.two_scopes_with_a_folder()
        values, last = adapter.ldb_state(live)
        adapter.ldb_put(live, [(adapter.ls_key('ccd-sync-owner'), adapter.ls_value(self.B_ID))], last)
        self.assertTrue(adapter.sync_conversations(self.data, self.root)['Folders'])
        self.assertNotIn(adapter.ls_key(adapter.SYNC_PENDING_KEY), adapter.ldb_state(live)[0])

    def test_failed_verification_restores_everything_and_keeps_the_backup(self):
        live, scope_a, scope_b, folder, empty = self.two_scopes_with_a_folder()
        indexes = {p: p.read_bytes() for p in (self.official / 'claude-code-sessions').rglob('*') if p.is_file()}
        transcripts = {p: p.read_bytes() for p in self.user.rglob('*.jsonl')}
        store = adapter.ldb_state(live)
        with patch.object(adapter, 'verify_sync', return_value='fixture problem'):
            with self.assertRaisesRegex(ValueError, '已恢复到同步前的状态'):
                adapter.sync_conversations(self.data, self.root)
        self.assertEqual({p: p.read_bytes() for p in (self.official / 'claude-code-sessions').rglob('*') if p.is_file()}, indexes)
        self.assertEqual({p: p.read_bytes() for p in self.user.rglob('*.jsonl')}, transcripts)
        self.assertEqual(adapter.ldb_state(live), store)
        journal = adapter.read(next((self.data / 'conversation-sync').glob('*.json')))
        self.assertEqual((journal['Status'], journal['Problem']), ('rolled-back', 'fixture problem'))
        backup = next((self.data / 'conversation-sync-originals').iterdir())
        self.assertTrue((backup / 'Local Storage/000003.log').is_file())
        self.assertTrue(any(backup.rglob('local_*.json')))

    def test_first_account_leads_and_same_named_folders_merge(self):
        """A logged in first. B's own folders and B-only conversations are added; shared ones follow A."""
        org_a, org_b = 'aaaaaaaa-0000-4000-8000-000000000001', 'bbbbbbbb-0000-4000-8000-000000000002'
        scope_a, scope_b = f'{self.A_ID}/{org_a}', f'{self.B_ID}/{org_b}'
        ids = {k: f'7000000{i}-0000-4000-8000-000000000001' for i, k in enumerate(('both_a', 'both_free', 'only_b', 'tool_b', 'cwd_only'))}
        code = {k: 'code:local_' + v for k, v in ids.items()}
        (self.user / '.claude/projects/p').mkdir(parents=True)
        for k, sid in ids.items():
            (self.user / '.claude/projects/p' / f'cli-{k}.jsonl').write_text('{}', encoding='utf-8')
            cwd = 'E:\\work\\useful tools' if k in ('tool_b', 'cwd_only') else 'E:\\work\\other'
            if k not in ('only_b', 'tool_b'):
                self.session(self.A_ID, org_a, sid, f'cli-{k}', cwd=cwd)
            if k != 'cwd_only':
                self.session(self.B_ID, org_b, sid, f'cli-{k}', cwd=cwd)
        # The first account is decided by when each account first used Desktop here, not by its ID.
        first_use = {self.A_ID: 9, self.B_ID: 1}
        since = patch.object(adapter, 'account_since', lambda folder: first_use[folder.name])
        since.start(); self.addCleanup(since.stop)
        organizations = adapter.index_copies(self.official / 'claude-code-sessions')[0]
        self.assertEqual(adapter.primary_account(organizations), self.B_ID)
        first_use.update({self.A_ID: 1, self.B_ID: 9})
        groups_a = {'groups': [{'id': 'cg-u', 'name': 'Useful Tools'}, {'id': 'cg-p', 'name': 'Papers'}],
                    'assignments': {code['both_a']: 'cg-p'}, 'order': {'cg-p': [code['both_a']], 'cg-u': []}}
        groups_b = {'groups': [{'id': 'cg-x', 'name': 'useful tools '}, {'id': 'cg-b', 'name': 'B only'}],
                    'assignments': {code['both_a']: 'cg-b', code['both_free']: 'cg-b', code['only_b']: 'cg-b', code['tool_b']: 'cg-x'},
                    'order': {'cg-b': [code['both_a'], code['both_free'], code['only_b']], 'cg-x': [code['tool_b']]}}
        live = self.official / 'Local Storage/leveldb'
        (self.official / 'Local Storage/sample').unlink(); (self.official / 'Local Storage').rmdir()
        # B is logged in now and changed its folders last; A's layout is in its saved login.
        self.desktop_folders(live, self.B_ID, scope_b, {scope_b: groups_b}, {}, 900)
        self.desktop_folders(self.profiles / 'desktop-A' / 'Local Storage/leveldb', self.A_ID, scope_a, {scope_a: groups_a}, {}, 100)
        conversations = {p: p.read_bytes() for p in self.user.rglob('*.jsonl')}
        adapter.sync_conversations(self.data, self.root)
        result = self.frame(live)['state']['customGroupsByScope']
        self.assertEqual(result[scope_a], result[scope_b])
        layout = result[scope_a]
        names = {g['id']: g['name'] for g in layout['groups']}
        self.assertEqual(sorted(names.values()), ['B only', 'Papers', 'Useful Tools'])  # "useful tools " is A's "Useful Tools".
        placed = {k: names.get(layout['assignments'].get(c)) for k, c in code.items()}
        self.assertEqual(placed, {'both_a': 'Papers',            # shared: A's folder
                                  'both_free': None,             # shared and unfiled in A: stays by directory
                                  'only_b': 'B only',            # B-only: kept in B's own folder
                                  'tool_b': 'Useful Tools',      # B-only, B's same-named folder maps to A's
                                  'cwd_only': 'Useful Tools'})   # unfiled, directory named like a folder
        self.assertEqual(layout['order']['cg-b'], [code['only_b']])
        self.assertEqual({p: p.read_bytes() for p in conversations}, conversations)
        self.assertEqual(adapter.read(self.data / 'conversation-sync-state.json')['PrimaryAccount'], self.A_ID)

    def test_snappy_blocks_and_torn_logs(self):
        self.assertEqual(adapter.unsnappy(b'\x0c\x0cabcd\x11\x04'), b'abcdabcdabcd')
        with self.assertRaises(ValueError):
            adapter.unsnappy(b'\x0c\x0cabcd\x11\x09')
        folder = self.root / 'store/leveldb'
        self.ldb_store(folder, self.A_ID, 'a/b', {'k': {'v': 1}})
        log = folder / '000003.log'
        log.write_bytes(log.read_bytes() + b'\x01\x02\x03\x04\x05\x06\x07\x08')
        with self.assertRaisesRegex(ValueError, '末尾不完整'):
            adapter.ldb_put(folder, [(b'x', b'y')], 5)

    def test_switch_can_share_conversations_while_claude_is_closed(self):
        a = self.two_accounts()
        (self.user / '.claude/projects/p').mkdir(parents=True)
        (self.user / '.claude/projects/p/cli-b.jsonl').write_text('{}', encoding='utf-8')
        sid = '20000000-0000-4000-8000-000000000001'
        self.session(self.B_ID, 'cccccccc-0000-4000-8000-000000000003', sid, 'cli-b')
        (self.official / 'claude-code-sessions' / self.A_ID / 'dddddddd-0000-4000-8000-000000000004').mkdir(parents=True)
        self.running()
        result = adapter.execute(self.request('switch', Name=a['Profile'], QuitClaude=True, RelaunchClaude=True, SyncConversations=True), self.data, self.root)
        self.assertIn('已补齐 1 条对话到 1 个账号的列表', result['Message'])
        self.assertTrue(list((self.official / 'claude-code-sessions' / self.A_ID).glob(f'*/local_{sid}.json')))
        self.assertEqual([list(entry) for entry in self.process_log()], [['Quit'], ['Launch']])

    def test_quit_and_launch_subcommands_for_the_backup_flow(self):
        self.running()
        script = str(Path(adapter.__file__).resolve())
        run = lambda *args: adapter.subprocess.run([adapter.sys.executable, '-I', script, *args, '--fixture', str(self.root)],
                                                   capture_output=True, text=True, encoding='utf-8')
        quit_result = run('quit')
        self.assertEqual(quit_result.returncode, 0)
        target = json.loads(quit_result.stdout)['Target']
        self.assertEqual(target, {'Kind': 'appx', 'Id': 'Claude_abcdefghijk12!Claude'})
        self.assertEqual(adapter.read(self.root / 'processes.json'), [])
        launched = run('launch', '--target', json.dumps(target))
        self.assertTrue(json.loads(launched.stdout)['Launched'])
        self.assertEqual([list(entry) for entry in self.process_log()], [['Quit'], ['Launch']])
        adapter.atomic_json(self.root / 'processes.json', [{'Pid': 9, 'ParentPid': 1, 'Path': None}])
        refused = run('quit')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('无法识别', json.loads(refused.stdout)['Error'])

    def test_official_pricing_table_is_parsed_and_bad_pages_are_rejected(self):
        page = '\n'.join(['---', 'title: Pricing', '---', '## Model pricing', '',
            '| Model                | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |',
            '| :---                 | :---              | :---            | :---            | :---                     | :---          |',
            '| Claude Opus 5.5      | $4 / MTok | $5 / MTok | $8 / MTok | $0.20 / MTok<sup>2</sup> | $20 / MTok |',
            '| Claude Opus 4.1 ([retired, except on Bedrock](https://example.test)) | $15 / MTok | $18.75 / MTok | $30 / MTok | $1.50 / MTok | $75 / MTok |',
            '| Claude Sonnet 5.5    | $2 / MTok | $2.50 / MTok | $4 / MTok | $0.10 / MTok | $10 / MTok |',
            '| Claude Haiku 5.5 (for prompts up to 100,000 tokens) | $0.10 / MTok | $0.125 / MTok | $0.20 / MTok | $0.01 / MTok | $0.50 / MTok |',
            '| Claude Haiku 5.5 (for prompts over 100,000 tokens) | $0.50 / MTok | $0.625 / MTok | $1 / MTok | $0.05 / MTok | $2.50 / MTok |',
            '| Claude Haiku 4.5     | $1 / MTok | $1.25 / MTok | $2 / MTok | $0.10 / MTok | $5 / MTok |',
            '| Claude Fable 5.1     | $10 / MTok | $12.50 / MTok | $20 / MTok | $0.25 / MTok<sup>1</sup> | $50 / MTok |',
            '', '## Batch processing', '| Claude Opus 5.5 | $2 / MTok | $10 / MTok |'])
        models = adapter.parse_pricing(page)
        self.assertEqual(models['claude-opus-5-5'], {'Input': 4.0, 'CacheWrite5m': 5.0, 'CacheWrite1h': 8.0, 'CacheRead': 0.2, 'Output': 20.0})
        self.assertEqual(models['claude-opus-4-1']['Output'], 75.0)
        self.assertEqual(models['claude-haiku-5-5']['LongPrompt']['Input'], 0.5)
        self.assertEqual(models['claude-haiku-5-5']['Input'], 0.1)
        adapter.atomic_json(self.data / 'pricing-official.json', {'Models': {'claude-opus-5-5': {'Input': 4.0, 'Fast': {'Input': 8.0, 'Output': 40.0}}}})
        adapter.update_pricing(self.data, fetch=lambda url: page)
        saved = adapter.read(self.data / 'pricing-official.json')
        self.assertEqual(saved['Models']['claude-opus-5-5']['Fast'], {'Input': 8.0, 'Output': 40.0})
        for broken in ('no table here', page.replace('$4 / MTok', 'four dollars')):
            with self.assertRaises(ValueError):
                adapter.update_pricing(self.data, fetch=lambda url, text=broken: text)
        self.assertEqual(adapter.read(self.data / 'pricing-official.json'), saved)

    # ---- Codex accounts (synthetic tokens; the JWT payload is only decoded for display) ----
    def codex_login(self, account, email, refresh='refresh-fixture'):
        import base64
        claims = {'email': email, 'https://api.openai.com/auth': {'chatgpt_account_id': account, 'chatgpt_plan_type': 'pro'}}
        token = 'h.' + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=') + '.s'
        adapter.atomic_json(self.root / 'codex/auth.json', {'auth_mode': 'chatgpt', 'OPENAI_API_KEY': None,
            'tokens': {'id_token': token, 'access_token': 'access-' + refresh, 'refresh_token': refresh, 'account_id': account}})

    def codex_running(self, *extra):
        app = 'C:\\Program Files\\WindowsApps\\OpenAI.Codex_26.1.0.0_x64__2p2nqsd0c76g0\\app\\ChatGPT.exe'
        rows = [{'Pid': 200, 'ParentPid': 4, 'Path': app}, {'Pid': 201, 'ParentPid': 200, 'Path': 'C:\\Users\\fixture\\AppData\\Local\\OpenAI\\Codex\\bin\\x\\codex.exe'}]
        adapter.atomic_json(self.root / 'codex-processes.json', rows + list(extra))

    def test_codex_accounts_save_switch_and_add_with_quota(self):
        a, b = 'acct-aaaaaaaa-1111', 'acct-bbbbbbbb-2222'
        self.codex_login(a, 'a@example.test', 'refresh-a1')
        session = self.root / 'codex/sessions/2026/10/28/rollout-1.jsonl'
        session.parent.mkdir(parents=True)
        session.write_text('\n'.join([json.dumps({'type': 'session_meta', 'payload': {'creator_account_id': a}}),
            json.dumps({'type': 'event_msg', 'timestamp': '2026-10-28T10:00:00Z', 'payload': {'type': 'token_count', 'rate_limits':
                {'primary': {'used_percent': 32.0, 'window_minutes': 10080, 'resets_at': 1793774914}}}})]), encoding='utf-8')
        adapter.execute(self.request('codex-save'), self.data, self.root)
        self.codex_running()
        result = adapter.execute(self.request('codex-login-new', QuitClaude=True, RelaunchClaude=True), self.data, self.root)
        self.assertFalse((self.root / 'codex/auth.json').exists())
        self.assertEqual(result['OutgoingCheckpoint']['Account'], a)
        self.assertEqual(self.process_log()[-1], {'Launch': {'Kind': 'appx', 'Id': 'OpenAI.Codex_2p2nqsd0c76g0!App'}})
        self.codex_login(b, 'b@example.test', 'refresh-b1')
        adapter.execute(self.request('codex-save'), self.data, self.root)
        self.codex_login(a, 'a@example.test', 'refresh-a2')  # Codex refreshed A's token since it was saved.
        catalog = adapter.codex_catalog(self.data, self.root)
        self.assertEqual([r['Email'] for r in catalog['Accounts']], ['a@example.test', 'b@example.test'])
        self.assertEqual(catalog['Accounts'][0]['RateLimits']['Windows'][0]['Label'], '每周')
        self.assertEqual(adapter.read(self.data / 'codex-accounts.json')[b], 'b@example.test')
        adapter.execute(self.request('codex-switch', Account=b), self.data, self.root)
        self.assertEqual(adapter.codex_identity(adapter.read(self.root / 'codex/auth.json'))['Account'], b)
        # The outgoing account was checkpointed first, so switching back restores the refreshed token.
        adapter.execute(self.request('codex-switch', Account=a), self.data, self.root)
        self.assertEqual(adapter.read(self.root / 'codex/auth.json')['tokens']['refresh_token'], 'refresh-a2')

    def fake_codex_cli(self, writes_login=True):
        # Stands in for the official CLI: prints a sign-in link, then writes a login into CODEX_HOME.
        script = self.root / 'fake_codex.py'
        script.write_text('\n'.join([
            'import base64, json, os, sys',
            'print("Open https://auth.openai.com/oauth/authorize?fixture=1", flush=True)',
            'if %r:' % writes_login,
            '    claims = {"email": "new@example.test", "https://api.openai.com/auth": {"chatgpt_account_id": "acct-cccccccc-3333", "chatgpt_plan_type": "plus"}}',
            '    token = "h." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".s"',
            '    auth = {"auth_mode": "chatgpt", "tokens": {"id_token": token, "access_token": "a", "refresh_token": "r-new", "account_id": "acct-cccccccc-3333"}}',
            '    open(os.path.join(os.environ["CODEX_HOME"], "auth.json"), "w").write(json.dumps(auth))']), encoding='utf-8')
        adapter.atomic_json(self.root / 'codex-cli.json', {'Command': [adapter.sys.executable, '-I', str(script)]})

    def test_codex_background_sign_in_adds_an_account_without_touching_the_live_login(self):
        import time as clock
        self.codex_login('acct-aaaaaaaa-1111', 'a@example.test', 'refresh-live')
        live_before = adapter.sha(self.root / 'codex/auth.json')
        self.codex_running()  # Codex keeps running throughout; nothing may be closed.
        self.fake_codex_cli()
        started = adapter.execute(self.request('codex-oauth-start'), self.data, self.root)
        self.assertIn('auth.openai.com', started['Url'])
        for _ in range(50):
            status = adapter.execute(self.request('codex-oauth-status', Id=started['Id']), self.data, self.root)
            if status['State'] != 'waiting':
                break
            clock.sleep(.1)
        self.assertEqual(status['State'], 'done')
        self.assertEqual(adapter.codex_identity(adapter.read(self.root / 'codex-profiles/acct-cccccccc-3333/auth.json'))['Email'], 'new@example.test')
        self.assertEqual(adapter.sha(self.root / 'codex/auth.json'), live_before)
        self.assertFalse((self.data / 'codex-oauth' / started['Id']).exists())
        self.assertEqual(self.process_log(), [])
        # Saving the live login also works while Codex is running.
        adapter.execute(self.request('codex-save'), self.data, self.root)
        self.assertTrue((self.root / 'codex-profiles/acct-aaaaaaaa-1111/auth.json').exists())
        self.assertEqual(self.process_log(), [])

    def test_codex_background_sign_in_failure_and_cancel_leave_nothing_behind(self):
        self.fake_codex_cli(writes_login=False)
        started = adapter.execute(self.request('codex-oauth-start'), self.data, self.root)
        (self.data / 'codex-oauth' / started['Id'] / 'exited').write_text('', encoding='utf-8')
        failed = adapter.execute(self.request('codex-oauth-status', Id=started['Id']), self.data, self.root)
        self.assertEqual(failed['State'], 'failed')
        again = adapter.execute(self.request('codex-oauth-start'), self.data, self.root)
        adapter.execute(self.request('codex-oauth-cancel', Id=again['Id']), self.data, self.root)
        self.assertEqual(list((self.data / 'codex-oauth').iterdir()), [])
        self.assertFalse((self.root / 'codex-profiles').exists())
        with self.assertRaises(ValueError):
            adapter.execute(self.request('codex-oauth-cancel', Id='../../etc'), self.data, self.root)

    def test_bulk_toggles_turn_all_skills_off_and_back_on(self):
        for name in ('alpha', 'beta', 'gamma'):
            folder = self.user / '.claude/skills' / name
            folder.mkdir(parents=True); (folder / 'SKILL.md').write_text(f'---\ndescription: {name}\n---', encoding='utf-8')
        result = adapter.execute(self.request('skill-bulk', App='codex', Enabled=True), self.data, self.root)
        self.assertEqual(sorted(result['Changed']), ['alpha', 'beta', 'gamma'])
        adapter.execute(self.request('skill-bulk', App='claude', Enabled=False), self.data, self.root)
        rows = adapter.skills_catalog(self.data, self.root)['Skills']
        self.assertEqual({(r['Name'], r['Claude'], r['Codex']) for r in rows}, {(n, False, True) for n in ('alpha', 'beta', 'gamma')})
        adapter.execute(self.request('skill-bulk', App='claude', Enabled=True), self.data, self.root)
        self.assertTrue(all(r['Claude'] for r in adapter.skills_catalog(self.data, self.root)['Skills']))
        adapter.atomic_json(self.user / '.claude.json', {'mcpServers': {'one': {'command': 'a'}, 'two': {'url': 'https://example.test/mcp'}}})
        adapter.execute(self.request('mcp-bulk', App='codex', Enabled=True), self.data, self.root)
        self.assertEqual(sorted(adapter.codex_servers((self.root / 'codex/config.toml').read_text(encoding='utf-8'))), ['one', 'two'])

    # ---- Antigravity (synthetic Antigravity Tools files; tokens are fixture strings) ----
    def antigravity(self):
        root = self.root / 'antigravity_tools'
        (root / 'accounts').mkdir(parents=True)
        adapter.atomic_json(root / 'gui_config.json', {'language': 'zh', 'proxy': {'port': 8045, 'api_key': 'sk-fixture-proxy', 'enabled': True,
            'scheduling': {'mode': 'CacheFirst', 'max_wait_seconds': 50}, 'preferred_account_id': None}, 'circuit_breaker': {'enabled': True}})
        for ident, email in (('acc-1', 'one@example.test'), ('acc-2', 'two@example.test')):
            adapter.atomic_json(root / 'accounts' / f'{ident}.json', {'id': ident, 'email': email, 'token': {'refresh_token': 'google-secret-' + ident},
                'disabled': False, 'proxy_disabled': False, 'quota': {'subscription_tier': 'PRO', 'models': [{'name': 'gemini-3.8-flash-high', 'percentage': 40, 'reset_time': '2026-10-29T14:00:00Z'}]}})
        adapter.atomic_json(root / 'accounts.json', {'accounts': [], 'current_account_id': 'acc-1'})
        return root

    # ---- Third-party accounts and the access table (synthetic keys; DPAPI is the real Windows API) ----
    def vendor(self, **values):
        request = dict({'Vendor': 'custom', 'Name': '测试厂商', 'Anthropic': 'https://example.test/anthropic',
                        'OpenAI': 'https://example.test/v1', 'Codex': 'https://example.test/v1', 'Key': 'sk-fixture-vendor-123456',
                        'Model': 'model-a', 'Tiers': {'Opus': 'model-big', 'Sonnet': 'model-a', 'Haiku': ''}}, **values)
        return adapter.execute(self.request('vendor-save', **request), self.data, self.root)['Id']

    def test_vendor_accounts_keep_keys_encrypted_and_detect_models(self):
        first = self.vendor(Vendor='deepseek', Name='', Codex='')
        stored = (self.data / 'vendors.json').read_text(encoding='utf-8')
        self.assertNotIn('sk-fixture-vendor-123456', stored)
        row = adapter.vendor_account(self.data, first)
        self.assertEqual((row['Name'], row['KeyHint'], adapter.vendor_key(row)), ('DeepSeek', '…3456', 'sk-fixture-vendor-123456'))
        # Editing without a key keeps the encrypted one.
        self.vendor(Id=first, Name='DeepSeek 主力', Key='', Model='model-b', Codex='')
        self.assertEqual(adapter.vendor_key(adapter.vendor_account(self.data, first)), 'sk-fixture-vendor-123456')
        calls = []
        def fetch(url, headers):
            calls.append(url)
            if url.endswith('/v1/models') and 'anthropic' not in url:
                raise urllib.error.HTTPError(url, 404, 'nf', {}, None)
            return {'data': [{'id': 'model-b'}, {'id': 'model-a'}, {'id': 'bad id'}]}
        found = adapter.vendor_models({'Id': first}, self.data, self.root, fetch)
        self.assertEqual((found['Models'], found['Via']), (['model-a', 'model-b'], 'Anthropic'))
        self.assertEqual(calls, ['https://example.test/v1/models', 'https://example.test/anthropic/v1/models'])
        def denied(url, headers):
            raise urllib.error.HTTPError(url, 401, 'no', {}, None)
        with self.assertRaises(ValueError) as caught:
            adapter.vendor_models({'Anthropic': 'https://example.test/a', 'Key': 'sk-typed-secret'}, self.data, self.root, denied)
        self.assertIn('401', str(caught.exception))
        self.assertNotIn('sk-typed-secret', str(caught.exception))
        with self.assertRaises(ValueError):
            adapter.vendor_save({'Vendor': 'custom', 'Anthropic': 'http://example.test', 'Key': 'k-123456789'}, self.data, self.root)
        catalog = adapter.vendor_catalog(self.data, self.root)
        self.assertNotIn('sk-fixture', json.dumps(catalog, ensure_ascii=False))
        self.assertEqual((catalog['Accounts'][0]['CanClaude'], catalog['Accounts'][0]['CanCodex']), (True, False))
        self.assertEqual({t: r['Source'] for t, r in catalog['Routes'].items()}, {'claude-desktop': 'official', 'claude-code': 'official', 'codex': 'official'})
        # CC Switch providers are imported once, with their keys encrypted; CC Switch itself is untouched.
        with closing(sqlite3.connect(self.db)) as connection:
            codex = lambda key, url: json.dumps({'auth': {'OPENAI_API_KEY': key}, 'config': f'model_provider = "custom"\nmodel = "k3"\n[model_providers.custom]\nbase_url = "{url}"\nwire_api = "responses"\n',
                                                 'modelCatalog': {'models': [{'model': 'k3'}, {'model': 'k3-fast'}]}})
            connection.executemany('insert into providers values(?,?,?,?,?)', [
                ('kimi-codex', 'Kimi Codex', codex('secret-fixture-not-for-ui', 'https://api.kimi.com/coding/v1'), '{}', 'codex'),
                ('ds-codex', 'DeepSeek Codex', codex('sk-other-fixture-key', 'https://api.deepseek.com'), '{}', 'codex'),
                ('codex-official', 'OpenAI Official', json.dumps({'auth': {'auth_mode': 'chatgpt', 'tokens': {}}, 'config': 'model = "x"'}), '{}', 'codex')])
            connection.commit()
        before = adapter.sha(self.db)
        imported = adapter.vendor_import({}, self.data, self.root, self.db)
        adapter.vendor_import({}, self.data, self.root, self.db)
        self.assertIn('2 个账号', imported['Message'])
        rows = {r['Name']: r for r in adapter.vendor_accounts(self.data)}
        self.assertEqual(sorted(rows), ['DeepSeek Codex', 'DeepSeek 主力', 'Kimi 测试'])
        # The Codex provider with the same key filled in the Claude account's Codex address.
        self.assertEqual((rows['Kimi 测试']['Codex'], rows['Kimi 测试']['Models']), ('https://api.kimi.com/coding/v1', ['k3', 'k3-fast']))
        self.assertEqual((rows['DeepSeek Codex']['Vendor'], rows['DeepSeek Codex']['Codex'], rows['DeepSeek Codex']['Anthropic']), ('deepseek', 'https://api.deepseek.com', ''))
        self.assertNotIn('secret-fixture-not-for-ui', (self.data / 'vendors.json').read_text(encoding='utf-8'))
        self.assertEqual(adapter.sha(self.db), before)
        adapter.execute(self.request('vendor-delete', Id=first), self.data, self.root)
        self.assertEqual(len(adapter.vendor_accounts(self.data)), 2)
        # Accounts saved by the previous build (a Responses flag instead of a Codex address) still work.
        stored = adapter.read(self.data / 'vendors.json')
        stored['Accounts'][0].pop('Codex'); stored['Accounts'][0].update({'OpenAI': 'https://old.example.test/v1', 'Responses': True})
        adapter.atomic_json(self.data / 'vendors.json', stored)
        self.assertEqual(adapter.vendor_accounts(self.data)[0]['Codex'], 'https://old.example.test/v1')

    def test_routes_switch_claude_code_codex_and_desktop_and_restore_exactly(self):
        import tomllib
        original = {'env': {'ANTHROPIC_MODEL': 'mine', 'ANTHROPIC_API_KEY': 'user-own', 'KEEP': '1'}, 'theme': 'dark'}
        adapter.atomic_json(self.user / '.claude/settings.json', original)
        (self.root / 'codex').mkdir(parents=True, exist_ok=True)
        toml = '# keep me\nmodel = "gpt-6.1-sol"\napproval_policy = "never"\n\n[mcp_servers.node]\ncommand = "node"\n'
        (self.root / 'codex/config.toml').write_text(toml, encoding='utf-8')
        a, b = self.vendor(), self.vendor(Name='只有 Claude', OpenAI='', Codex='')
        adapter.execute(self.request('route-claude-code', Source=a), self.data, self.root)
        env = adapter.read(self.user / '.claude/settings.json')['env']
        self.assertEqual((env['ANTHROPIC_BASE_URL'], env['ANTHROPIC_AUTH_TOKEN'], env['ANTHROPIC_MODEL'], env['ANTHROPIC_DEFAULT_OPUS_MODEL'], env['KEEP']),
                         ('https://example.test/anthropic', 'sk-fixture-vendor-123456', 'model-a', 'model-big', '1'))
        self.assertNotIn('ANTHROPIC_API_KEY', env)
        self.assertNotIn('ANTHROPIC_DEFAULT_HAIKU_MODEL', env)
        adapter.execute(self.request('route-claude-code', Source=b, Model='model-c'), self.data, self.root)  # Vendor to vendor.
        with self.assertRaisesRegex(ValueError, 'Claude Code'):
            adapter.execute(self.request('vendor-delete', Id=b), self.data, self.root)
        # Codex: a vendor without Responses support is refused before anything changes.
        with self.assertRaisesRegex(ValueError, 'Codex'):
            adapter.execute(self.request('codex-route', Source=b), self.data, self.root)
        self.assertEqual((self.root / 'codex/config.toml').read_text(encoding='utf-8'), toml)
        adapter.execute(self.request('codex-route', Source=a), self.data, self.root)
        config = tomllib.loads((self.root / 'codex/config.toml').read_text(encoding='utf-8'))
        self.assertEqual((config['model_provider'], config['model'], config['model_providers']['studio']['wire_api']), ('studio', 'model-a', 'responses'))
        self.assertEqual((config['model_providers']['studio']['requires_openai_auth'], config['model_providers']['studio']['experimental_bearer_token']), (False, 'sk-fixture-vendor-123456'))
        self.assertIn('第三方', config['model_providers']['studio']['name'])
        catalog = adapter.vendor_catalog(self.data, self.root)
        self.assertEqual((catalog['Routes']['claude-code']['Source'], catalog['Routes']['codex']['Source']), (b, a))
        # Claude Desktop uses the same account as a direct third-party gateway.
        adapter.switch_provider({'ProviderId': 'vendor:' + a}, self.data, self.root)
        profile = adapter.read(self.third / 'configLibrary' / (adapter.PROFILE_ID + '.json'))
        self.assertEqual(profile['inferenceGatewayBaseUrl'], 'https://example.test/anthropic')
        self.assertEqual(adapter.vendor_catalog(self.data, self.root)['Routes']['claude-desktop']['Label'], '测试厂商')
        adapter.record_timeline(self.data, self.root)
        self.assertEqual(adapter.read(self.data / 'account-timeline.json')[-1]['Provider'], '测试厂商')
        for target in ('route-claude-code', 'codex-route'):
            adapter.execute(self.request(target, Source='official'), self.data, self.root)
        self.assertEqual(adapter.read(self.user / '.claude/settings.json'), original)
        self.assertEqual((self.root / 'codex/config.toml').read_text(encoding='utf-8').strip(), toml.strip())
        self.assertEqual(adapter.read(self.data / 'routes.json'), {})

    def webdav(self, user='u', password='app-password'):
        """A small WebDAV server over a temporary folder, enough for MKCOL/PUT/GET/DELETE/PROPFIND."""
        import base64, html
        store = self.root / 'dav'
        store.mkdir()
        expected = 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode()
        class Dav(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def target(self):
                if self.headers.get('Authorization') != expected:
                    self.send_response(401); self.send_header('Content-Length', '0'); self.end_headers()
                    return None
                return store / urllib.parse.unquote(self.path.split('/dav/', 1)[1])
            def done(self, code, body=b''):
                self.send_response(code); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_MKCOL(self):
                path = self.target()
                if path is None: return
                if path.exists(): return self.done(405)
                path.mkdir(); self.done(201)
            def do_PUT(self):
                path = self.target()
                if path is None: return
                path.write_bytes(self.rfile.read(int(self.headers['Content-Length']))); self.done(201)
            def do_GET(self):
                path = self.target()
                if path is None: return
                self.done(200, path.read_bytes()) if path.is_file() else self.done(404)
            def do_DELETE(self):
                path = self.target()
                if path is None: return
                path.unlink(); self.done(204)
            def do_PROPFIND(self):
                path = self.target()
                if path is None: return
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                rows = ''.join(f'<d:response><d:href>/dav/{urllib.parse.quote(path.name)}/{urllib.parse.quote(f.name)}</d:href><d:propstat><d:prop>'
                               f'<d:getcontentlength>{f.stat().st_size}</d:getcontentlength></d:prop></d:propstat></d:response>' for f in path.iterdir())
                self.done(207, f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"><d:response><d:href>/dav/{urllib.parse.quote(path.name)}/</d:href></d:response>{rows}</d:multistatus>'.encode())
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Dav)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_address[1]}/dav', store

    def test_webdav_backups_are_encrypted_pruned_and_restored(self):
        url, store = self.webdav()
        settings = {'Url': url, 'User': 'u', 'Password': 'app-password', 'Passphrase': 'correct horse battery', 'Folder': '工具 备份', 'Mode': 'both', 'Keep': 1}
        adapter.execute(self.request('cloud-save', **settings), self.data, self.root)
        stored = (self.data / 'cloud.json').read_text(encoding='utf-8')
        self.assertNotIn('app-password', stored)
        self.assertNotIn('correct horse', stored)
        self.assertEqual(adapter.cloud_catalog(self.data)['Mode'], 'both')
        account = self.vendor()
        backup = self.data / 'backups' / '20261029-133031-123456'
        backup.mkdir(parents=True)
        adapter.atomic_json(backup / 'manifest.json', {'Title': '新增备份 1'})
        (backup / 'sessions').mkdir(); (backup / 'sessions' / 'a.jsonl').write_text('对话原文', encoding='utf-8')
        adapter.execute(self.request('cloud-upload', BackupId=backup.name), self.data, self.root)
        import time as clock
        clock.sleep(1.1)  # Remote names carry the second; the next upload must sort after the first.
        adapter.execute(self.request('cloud-upload', BackupId=backup.name), self.data, self.root)
        remote = sorted((store / '工具 备份').iterdir())
        self.assertEqual([f.name.split('-')[0] for f in remote], ['session', 'settings'], 'Keep=1 prunes per kind')
        for path in remote:
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(adapter.CLOUD_MAGIC))
            for secret in (b'sk-fixture-vendor', '对话原文'.encode(), b'manifest'):
                self.assertNotIn(secret, raw)
        listed = adapter.execute(self.request('cloud-list'), self.data, self.root)['Files']
        self.assertEqual({r['Kind'] for r in listed}, {'settings', 'session'})
        # Restore on a "fresh" machine: the local backup and the account are gone.
        import shutil as files
        files.rmtree(backup)
        adapter.atomic_json(self.data / 'vendors.json', {'Accounts': []})
        for row in listed:
            adapter.execute(self.request('cloud-restore', Name=row['Name']), self.data, self.root)
        self.assertEqual((backup / 'sessions' / 'a.jsonl').read_text(encoding='utf-8'), '对话原文')
        self.assertEqual(adapter.vendor_key(adapter.vendor_account(self.data, account)), 'sk-fixture-vendor-123456')
        self.assertNotIn('PlainKey', (self.data / 'vendors.json').read_text(encoding='utf-8'))
        self.assertIn('已经有', adapter.execute(self.request('cloud-restore', Name=[r for r in listed if r['Kind'] == 'session'][0]['Name']), self.data, self.root)['Message'])
        self.assertEqual(list((self.data / 'cloud-temp').iterdir()), [])
        # A wrong passphrase or a changed file is refused before anything is written.
        adapter.execute(self.request('cloud-save', **dict(settings, Passphrase='another passphrase')), self.data, self.root)
        with self.assertRaisesRegex(ValueError, '加密密码不对'):
            adapter.execute(self.request('cloud-restore', Name=listed[0]['Name']), self.data, self.root)
        adapter.execute(self.request('cloud-save', **settings), self.data, self.root)
        target = store / '工具 备份' / listed[0]['Name']
        raw = bytearray(target.read_bytes()); raw[60] ^= 1; target.write_bytes(bytes(raw))
        with self.assertRaisesRegex(ValueError, '加密密码不对|损坏'):
            adapter.execute(self.request('cloud-restore', Name=listed[0]['Name']), self.data, self.root)
        # Another machine's newer backup is announced once.
        other = target.with_name(target.name.replace(adapter.machine_tag(), 'abcdef12'))
        target.rename(other)
        self.assertEqual(len(adapter.execute(self.request('cloud-check'), self.data, self.root)['Newer']), 1)
        self.assertEqual(adapter.execute(self.request('cloud-check'), self.data, self.root)['Newer'], [])
        with self.assertRaisesRegex(ValueError, '用户名或密码不对'):
            adapter.execute(self.request('cloud-save', **dict(settings, Password='wrong')), self.data, self.root)
            adapter.execute(self.request('cloud-list'), self.data, self.root)

    def test_connections_from_the_old_antigravity_page_restore_exactly(self):
        import tomllib
        (self.root / 'codex').mkdir(parents=True, exist_ok=True)
        toml = 'model = "gemini-x"\nmodel_provider = "antigravity"\n\n[model_providers.antigravity]\nname = "A"\nbase_url = "http://127.0.0.1:8045/v1"\n'
        (self.root / 'codex/config.toml').write_text(toml, encoding='utf-8')
        adapter.atomic_json(self.data / 'agy-connections.json', {'codex': {'Previous': {'model_provider': None, 'model': 'gpt-6.1-sol'}, 'Model': 'gemini-x'}})
        self.assertEqual(adapter.vendor_catalog(self.data, self.root)['Routes']['codex']['Label'], 'Antigravity Tools（本机代理）')
        adapter.execute(self.request('codex-route', Source='official'), self.data, self.root)
        self.assertEqual(tomllib.loads((self.root / 'codex/config.toml').read_text(encoding='utf-8')), {'model': 'gpt-6.1-sol'})
        self.assertEqual(adapter.read(self.data / 'agy-connections.json'), {})
        self.assertEqual(adapter.vendor_catalog(self.data, self.root)['Routes']['codex']['Source'], 'official')

    def test_google_prices_by_date_and_antigravity_usage_cost(self):
        import sqlite3
        page = ('<h2>Gemini 9 Flash</h2><code>gemini-9-flash</code><p>Standard</p><table><tr><td>Free Tier</td><td>Paid Tier, per 1M tokens in USD</td></tr>'
                '<tr><td>Input price</td><td>Free of charge</td><td>$1.00 through December 31, 2026.</td><td>$2.00 starting January 1, 2027.</td></tr>'
                '<tr><td>Output price (including thinking tokens)</td><td>$10.00 through December 31, 2026.</td><td>$20.00 starting January 1, 2027.</td></tr>'
                '<tr><td>Context caching price</td><td>$0.10 through December 31, 2026.</td><td>$0.20 starting January 1, 2027.</td>'
                '<td>$1.00 / 1,000,000 tokens per hour (storage price)</td></tr></table><p>Batch</p><table><tr><td>Input price</td><td>$0.50</td></tr></table>'
                '<code>gemini-9-pro-preview</code><code>gemini-9-pro-preview-customtools</code><p>Standard</p>'
                '<tr><td>Input price</td><td>$3.00, prompts &lt;= 200k tokens</td><td>$6.00, prompts &gt; 200k tokens</td></tr>'
                '<tr><td>Output price (including thinking tokens)</td><td>$15.00, prompts &lt;= 200k tokens</td><td>$22.50, prompts &gt; 200k</td></tr>'
                + ''.join(f'<code>gemini-filler-{i}</code><p>Standard</p><td>Input price</td><td>$1</td><td>Output price</td><td>$2</td>' for i in range(4)))
        models = adapter.parse_google_pricing(page)
        self.assertEqual([p['Input'] for p in models['gemini-9-flash']['Periods']], [1.0, 2.0])
        self.assertEqual(models['gemini-9-flash']['Periods'][0]['CacheRead'], 0.1)
        self.assertEqual(models['gemini-9-pro']['Periods'][0]['LongPrompt']['Input'], 6.0, 'shared with the -customtools block')
        root = self.antigravity()
        adapter.atomic_json(self.data / 'pricing-google.json', {'FetchedAt': 'test', 'Models': models})
        adapter.atomic_json(self.data / 'pricing-official.json', {'Models': {'claude-sonnet-4-6': {'Input': 3.0, 'Output': 15.0, 'CacheRead': 0.3}}})
        from datetime import datetime as moment
        with closing(sqlite3.connect(root / 'token_stats.db')) as db:
            db.execute('create table token_usage(id integer, timestamp integer, account_email text, model text, input_tokens integer, '
                       'output_tokens integer, cached_tokens integer, total_tokens integer, reasoning_tokens integer)')
            rows = [(moment(2026, 6, 1).timestamp(), 'one@example.test', 'gemini-9-flash-high', 1_000_000, 1_000_000, 500_000),
                    (moment(2027, 2, 1).timestamp(), 'one@example.test', 'gemini-9-flash-low', 1_000_000, 0, 0),
                    (moment(2026, 6, 1).timestamp(), 'two@example.test', 'claude-sonnet-4-6-thinking', 1_000_000, 0, 0),
                    (moment(2026, 6, 1).timestamp(), 'two@example.test', 'gpt-oss-120b-medium', 10, 10, 0)]
            db.executemany('insert into token_usage values(null,?,?,?,?,?,?,0,0)', [(int(t), e, m, i, o, c) for t, e, m, i, o, c in rows])
            db.commit()
        usage = adapter.agy_usage({'Days': 0}, self.data, self.root)
        # 0.5M uncached*$1 + 0.5M cached*$0.10 + 1M out*$10 = 10.55; then 1M at the 2027 price $2; Claude 1M*$3.
        self.assertEqual(usage['Totals']['CostText'], '$15.55+')
        self.assertEqual({r['Name']: r['CostText'] for r in usage['Accounts']}, {'one@example.test': '$12.55', 'two@example.test': '$3.00+'})
        self.assertEqual(usage['Totals']['CacheRead'], 500_000)

    def test_independent_codex_cli_blocks_and_is_never_closed(self):
        self.codex_login('acct-aaaaaaaa-1111', 'a@example.test')
        adapter.execute(self.request('codex-save'), self.data, self.root)
        self.codex_running({'Pid': 300, 'ParentPid': 7, 'Path': 'C:\\Users\\fixture\\AppData\\Roaming\\npm\\codex.exe'})
        self.assertFalse(adapter.process_summary(self.root, 'codex')['CanQuit'])
        with self.assertRaisesRegex(ValueError, '命令行'):
            adapter.execute(self.request('codex-login-new', QuitClaude=True, RelaunchClaude=True), self.data, self.root)
        self.assertEqual(self.process_log(), [])
        self.assertTrue((self.root / 'codex/auth.json').exists())

    # ---- MCP servers ----
    def test_mcp_toggle_moves_servers_between_apps_and_keeps_other_settings(self):
        adapter.atomic_json(self.user / '.claude.json', {'oauthAccount': {'keep': True}, 'mcpServers': {
            'docs': {'type': 'stdio', 'command': 'npx', 'args': ['-y', 'docs-mcp'], 'env': {'TOKEN_FILE': 'C:\\keys\\a.txt'}}}})
        toml = ('# user comment kept\nmodel = "gpt-test"\n\n[mcp_servers]\n\n[mcp_servers.node_repl]\ncommand = "node"\n'
                'args = ["repl.js"]\n\n[mcp_servers.node_repl.env]\nNODE_PATH = "C:\\\\node"\n\n[projects."E:\\\\work"]\ntrust_level = "trusted"\n')
        (self.root / 'codex').mkdir(parents=True, exist_ok=True)
        (self.root / 'codex/config.toml').write_text(toml, encoding='utf-8')
        catalog = {r['Name']: r for r in adapter.mcp_catalog(self.data, self.root)['Servers']}
        self.assertEqual((catalog['docs']['Claude'], catalog['docs']['Codex']), (True, False))
        adapter.execute(self.request('mcp-toggle', Name='docs', App='codex', Enabled=True), self.data, self.root)
        text = (self.root / 'codex/config.toml').read_text(encoding='utf-8')
        self.assertIn('# user comment kept', text)
        servers = adapter.codex_servers(text)
        self.assertEqual(servers['docs'], {'command': 'npx', 'args': ['-y', 'docs-mcp'], 'env': {'TOKEN_FILE': 'C:\\keys\\a.txt'}})
        self.assertEqual(servers['node_repl']['env'], {'NODE_PATH': 'C:\\node'})
        adapter.execute(self.request('mcp-toggle', Name='node_repl', App='codex', Enabled=False), self.data, self.root)
        import tomllib
        after = tomllib.loads((self.root / 'codex/config.toml').read_text(encoding='utf-8'))
        self.assertNotIn('node_repl', after['mcp_servers'])
        self.assertEqual(after['projects']['E:\\work'], {'trust_level': 'trusted'})
        adapter.execute(self.request('mcp-toggle', Name='node_repl', App='codex', Enabled=True), self.data, self.root)
        self.assertEqual(adapter.codex_servers((self.root / 'codex/config.toml').read_text(encoding='utf-8'))['node_repl'],
                         {'command': 'node', 'args': ['repl.js'], 'env': {'NODE_PATH': 'C:\\node'}})
        adapter.execute(self.request('mcp-toggle', Name='docs', App='claude', Enabled=False), self.data, self.root)
        claude = adapter.read(self.user / '.claude.json')
        self.assertEqual((claude['mcpServers'], claude['oauthAccount']), ({}, {'keep': True}))
        adapter.execute(self.request('mcp-toggle', Name='docs', App='claude', Enabled=True), self.data, self.root)
        self.assertEqual(adapter.read(self.user / '.claude.json')['mcpServers']['docs']['env'], {'TOKEN_FILE': 'C:\\keys\\a.txt'})

    def test_mcp_toggle_refuses_inline_tables_without_touching_the_file(self):
        (self.root / 'codex').mkdir(parents=True, exist_ok=True)
        inline = 'mcp_servers = { tool = { command = "x" } }\n'
        (self.root / 'codex/config.toml').write_text(inline, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '无法安全'):
            adapter.execute(self.request('mcp-toggle', Name='tool', App='codex', Enabled=False), self.data, self.root)
        self.assertEqual((self.root / 'codex/config.toml').read_text(encoding='utf-8'), inline)

    # ---- Skills ----
    def test_skill_toggle_copies_between_apps_and_keeps_a_library_copy(self):
        skill = self.user / '.claude/skills/paper-reader'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: paper-reader\ndescription: Reads papers\n---\nBody', encoding='utf-8')
        (skill / 'scripts').mkdir(); (skill / 'scripts/run.py').write_text('print(1)', encoding='utf-8')
        (self.root / 'codex/skills/.system').mkdir(parents=True)
        rows = {r['Name']: r for r in adapter.skills_catalog(self.data, self.root)['Skills']}
        self.assertEqual(list(rows), ['paper-reader'])
        self.assertEqual(rows['paper-reader']['Description'], 'Reads papers')
        adapter.execute(self.request('skill-toggle', Name='paper-reader', App='codex', Enabled=True), self.data, self.root)
        self.assertEqual(adapter.tree_digest(self.root / 'codex/skills/paper-reader'), adapter.tree_digest(skill))
        digest = adapter.tree_digest(skill)
        adapter.execute(self.request('skill-toggle', Name='paper-reader', App='claude', Enabled=False), self.data, self.root)
        adapter.execute(self.request('skill-toggle', Name='paper-reader', App='codex', Enabled=False), self.data, self.root)
        self.assertFalse(skill.exists())
        self.assertEqual(adapter.tree_digest(self.data / 'skills-library/paper-reader'), digest)
        adapter.execute(self.request('skill-toggle', Name='paper-reader', App='claude', Enabled=True), self.data, self.root)
        self.assertEqual(adapter.tree_digest(skill), digest)
        with self.assertRaises(ValueError):
            adapter.execute(self.request('skill-toggle', Name='../escape', App='claude', Enabled=True), self.data, self.root)

    def test_add_account_prepares_blank_login_and_keeps_both_accounts(self):
        a = self.two_accounts()
        self.running()
        result = adapter.execute(self.request('login-new', QuitClaude=True, RelaunchClaude=True), self.data, self.root)
        self.assertEqual(result['OutgoingCheckpoint']['Account'], self.B_ID)
        blank = adapter.read(self.official / 'config.json')
        self.assertEqual(blank, {'locale': 'zh-CN'})
        for item in ('Network', 'Local Storage', 'Session Storage'):
            self.assertFalse((self.official / item).exists())
        self.assertTrue((self.official / 'Local State').exists())
        self.assertEqual([list(entry) for entry in self.process_log()], [['Quit'], ['Launch']])
        # The user now signs in to a third account inside Claude; Desktop recreates its folders.
        c_id = '33333333-3333-4333-8333-333333333333'
        for item in ('Network', 'Local Storage', 'Session Storage'):
            (self.official / item).mkdir(); (self.official / item / 'sample').write_text('C', encoding='utf-8')
        adapter.atomic_json(self.official / 'config.json', dict(blank, lastKnownAccountUuid=c_id, **{'oauth:tokenCacheV2': 'encrypted-fixture-C'}))
        adapter.execute(self.request('save'), self.data, self.root)
        saved = {adapter.read(p / 'config.json')['lastKnownAccountUuid'] for p in self.profiles.iterdir() if not p.name.startswith('.')}
        self.assertEqual(saved, {self.A_ID, self.B_ID, c_id})
        adapter.execute(self.request('switch', Name='desktop-' + self.B_ID, ExpectedAccount=self.B_ID), self.data, self.root)
        self.assertEqual(adapter.read(self.official / 'config.json')['oauth:tokenCacheV2'], 'encrypted-fixture-B')

    def test_pending_transaction_blocks_new_mutations(self):
        adapter.atomic_json(self.data / 'private-auth-backups/pending/transaction.json', {'Status': 'applying'})
        with self.assertRaises(ValueError): adapter.reject_pending(self.data)

    def test_authentication_lock_is_shared_between_installations(self):
        with adapter.auth_operation(self.profiles):
            with self.assertRaisesRegex(ValueError, '另一个版本'):
                with adapter.auth_operation(self.profiles):
                    self.fail('A second installation must not acquire the same account lock')
        with adapter.auth_operation(self.profiles):
            pass

    def test_interrupted_profile_replacement_blocks_a_different_installation(self):
        saved = adapter.save_account({}, self.data, self.root)
        original_replace = adapter.replace_within
        def interrupt_install(source, destination, allowed_root):
            if Path(source).name.startswith('.studio-staging-'):
                raise KeyboardInterrupt('fixture abrupt interruption after previous profile rename')
            return original_replace(source, destination, allowed_root)
        with patch.object(adapter, 'replace_within', interrupt_install):
            with self.assertRaises(KeyboardInterrupt): adapter.save_account({}, self.data, self.root)
        self.assertFalse((self.profiles / saved['Profile']).exists())
        self.assertTrue(list(self.profiles.glob('.studio-previous-*')))
        with self.assertRaisesRegex(ValueError, '未完成'):
            adapter.reject_pending(self.root / 'another-version-data', self.profiles)

    def test_third_party_identity_is_not_saved_as_official(self):
        adapter.atomic_json(self.official / 'claude_desktop_config.json', {'deploymentMode': '3p'})
        with self.assertRaises(ValueError): adapter.save_account({'Name': 'wrong-identity'}, self.data, self.root)
        self.assertFalse(self.profiles.exists())

    @unittest.skipUnless(os.name=='nt','Windows cross-volume acceptance')
    def test_cross_volume_snapshot_save_replace_and_switch(self):
        with tempfile.TemporaryDirectory(prefix='claude-studio-cross-drive-',dir=tempfile.gettempdir()) as directory:
            other=Path(directory)
            self.assertEqual(other.resolve().parent,Path(tempfile.gettempdir()).resolve())
            if other.drive.lower()==self.data.drive.lower():self.skipTest('Need two different local drives')
            official,_,profiles,user=adapter.app_paths(other)
            for item in adapter.AUTH_ITEMS:
                target=official/item
                if item in ('Local State','config.json'):
                    adapter.atomic_json(target,{'lastKnownAccountUuid':'11111111-1111-4111-8111-111111111111','oauth:tokenCacheV2':'encrypted-fixture'} if item=='config.json' else {'fixture':True})
                else:
                    target.mkdir(parents=True);(target/'dummy').write_text('fixture',encoding='utf-8')
            adapter.atomic_json(user/'.claude.json',{'oauthAccount':{'accountUuid':'11111111-1111-4111-8111-111111111111','emailAddress':'fixture@example.test'}})
            saved=adapter.save_account({'Name':'cross-volume'},self.data,other)
            adapter.save_account({'Name':'cross-volume'},self.data,other)
            adapter.atomic_json(official/'config.json',{'lastKnownAccountUuid':'22222222-2222-4222-8222-222222222222'})
            adapter.switch_account({'Name':saved['Profile']},self.data,other)
            self.assertEqual(adapter.read(official/'config.json')['lastKnownAccountUuid'],'11111111-1111-4111-8111-111111111111')
            generated=official/'created-by-test.json';adapter.atomic_json(generated,{'fixture':True})
            archived=self.data/'cross-volume-archived.json';adapter.archive_file(generated,archived)
            self.assertFalse(generated.exists());self.assertEqual(adapter.read(archived),{'fixture':True})

    def test_bad_profile_names_rejected(self):
        for name in ('../escape', '..', 'CON', 'space ', 'a"b'):
            with self.assertRaises(ValueError):
                adapter.checked_name(name)

    def test_email_display_uses_stable_uuid_and_updates_existing_profile(self):
        account = adapter.read(self.official / 'config.json')['lastKnownAccountUuid']
        first = adapter.save_account({'ExpectedAccount': account, 'DisplayName': 'name+tag@example.test'}, self.data, self.root)
        second = adapter.save_account({'ExpectedAccount': account, 'DisplayName': 'renamed@example.test'}, self.data, self.root)
        self.assertEqual(first['Profile'], 'desktop-' + account)
        self.assertEqual(second['Profile'], first['Profile'])
        self.assertNotEqual(first['Backup'], second['Backup'])
        self.assertEqual((self.profiles / first['Profile'] / 'label.txt').read_text(encoding='utf-8'), 'renamed@example.test')
        self.assertEqual(len([p for p in self.profiles.iterdir() if not p.name.startswith('.')]), 1)

    def test_capture_wrong_identity_and_history_only_refused_before_backup(self):
        with self.assertRaisesRegex(ValueError, '不一致'):
            adapter.save_account({'ExpectedAccount': '22222222-2222-4222-8222-222222222222'}, self.data, self.root)
        adapter.atomic_json(self.official / 'config.json', {'lastKnownAccountUuid': '11111111-1111-4111-8111-111111111111'})
        with self.assertRaisesRegex(ValueError, 'Claude Code'):
            adapter.save_account({}, self.data, self.root)
        self.assertFalse(self.data.exists())
        self.assertFalse(self.profiles.exists())

    def test_switch_checkpoints_rotated_outgoing_login_and_roundtrips(self):
        a = adapter.save_account({}, self.data, self.root)
        b_id = '22222222-2222-4222-8222-222222222222'
        adapter.atomic_json(self.official / 'config.json', {'lastKnownAccountUuid': b_id, 'oauth:tokenCacheV2': 'encrypted-fixture-B-old'})
        b = adapter.save_account({}, self.data, self.root)
        adapter.atomic_json(self.official / 'config.json', {'lastKnownAccountUuid': b_id, 'oauth:tokenCacheV2': 'encrypted-fixture-B-rotated'})
        switched = adapter.switch_account({'Name': a['Profile'], 'ExpectedAccount': a['Account']}, self.data, self.root)
        self.assertEqual(switched['OutgoingCheckpoint']['Account'], b_id)
        self.assertEqual(adapter.read(self.profiles / b['Profile'] / 'config.json')['oauth:tokenCacheV2'], 'encrypted-fixture-B-rotated')
        adapter.switch_account({'Name': b['Profile'], 'ExpectedAccount': b_id}, self.data, self.root)
        self.assertEqual(adapter.read(self.official / 'config.json')['oauth:tokenCacheV2'], 'encrypted-fixture-B-rotated')

    def test_switch_wrong_card_and_missing_cache_do_not_touch_live_data(self):
        a = adapter.save_account({}, self.data, self.root)
        before = adapter.auth_fingerprint(self.official)
        with self.assertRaisesRegex(ValueError, '不一致'):
            adapter.switch_account({'Name': a['Profile'], 'ExpectedAccount': 'wrong-account'}, self.data, self.root)
        config = adapter.read(self.profiles / a['Profile'] / 'config.json')
        config.pop('oauth:tokenCacheV2')
        adapter.atomic_json(self.profiles / a['Profile'] / 'config.json', config)
        with self.assertRaisesRegex(ValueError, '登录缓存'):
            adapter.switch_account({'Name': a['Profile']}, self.data, self.root)
        self.assertEqual(adapter.auth_fingerprint(self.official), before)

    def test_stale_ui_does_not_restore_old_tokens_over_current_account(self):
        a = adapter.save_account({}, self.data, self.root)
        config = adapter.read(self.official / 'config.json')
        config['oauth:tokenCacheV2'] = 'encrypted-fixture-rotated-current'
        adapter.atomic_json(self.official / 'config.json', config)
        before = adapter.auth_fingerprint(self.official)
        adapter.switch_account({'Name': a['Profile'], 'ExpectedAccount': a['Account']}, self.data, self.root)
        self.assertEqual(adapter.auth_fingerprint(self.official), before)
        self.assertEqual(adapter.auth_fingerprint(self.profiles / a['Profile']), before)

    def test_snapshot_copy_failure_keeps_live_and_previous_profile(self):
        a = adapter.save_account({}, self.data, self.root)
        live_before = adapter.auth_fingerprint(self.official)
        profile_before = adapter.auth_fingerprint(self.profiles / a['Profile'])
        original_copy = adapter.shutil.copytree
        def fail_staging(source, destination, *args, **kwargs):
            if Path(destination).parent.name.startswith('.studio-staging-'):
                raise OSError('fixture copy failure')
            return original_copy(source, destination, *args, **kwargs)
        with patch.object(adapter.shutil, 'copytree', fail_staging):
            with self.assertRaises(OSError): adapter.save_account({}, self.data, self.root)
        self.assertEqual(adapter.auth_fingerprint(self.official), live_before)
        self.assertEqual(adapter.auth_fingerprint(self.profiles / a['Profile']), profile_before)

    def test_local_model_mapping_and_authentication(self):
        observed = []
        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                observed.append(body)
                value = json.dumps({'model': body['model'], 'usage': {'input_tokens': 2, 'output_tokens': 3}}).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(value))); self.end_headers(); self.wfile.write(value)
        upstream = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('update providers set settings_config=?,meta=?',
                       (json.dumps({'env': {'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{upstream.server_port}', 'ANTHROPIC_AUTH_TOKEN': 'fixture-key'}}),
                        json.dumps({'claudeDesktopMode': 'proxy', 'claudeDesktopModelRoutes': {'claude-fable-5': {'model': 'fixture-upstream-model'}}})))
            db.commit()
        adapter.atomic_json(self.data / 'relay.json', {'ProviderId': 'kimi-fixture', 'Database': str(self.db), 'GatewayToken': 'fixture-local-token'})
        relay = http.server.ThreadingHTTPServer(('127.0.0.1', 0), adapter.relay_handler(self.data))
        threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (upstream, relay)]
        for thread in threads: thread.start()
        try:
            url = f'http://127.0.0.1:{relay.server_port}/v1/messages'
            payload = json.dumps({'model': 'claude-fable-5', 'max_tokens': 4, 'messages': []}).encode()
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                urllib.request.urlopen(urllib.request.Request(url, payload), timeout=3)
            self.assertEqual(rejected.exception.code, 401)
            rejected.exception.close()
            request = urllib.request.Request(url, payload, {'Authorization': 'Bearer fixture-local-token', 'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=3) as response:
                result = json.load(response)
            self.assertEqual(observed[0]['model'], 'fixture-upstream-model')
            self.assertEqual(result['usage']['output_tokens'], 3)
        finally:
            for server in (relay, upstream): server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
