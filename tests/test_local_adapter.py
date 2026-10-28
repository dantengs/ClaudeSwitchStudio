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
