"""Offline acceptance tests. All installer writes and commands use disposable fixtures."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('support', ROOT / 'optimizer_support.py')
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='optimizer-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def file(self, name, content=b'old'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path


class SupportTests(Fixture):
    def test_atomic_create_update_backup_mode_and_idempotence(self):
        p = self.root / 'nested/config'
        self.assertTrue(support.atomic_write(p, b'one'))
        p.chmod(0o640)
        self.assertTrue(support.atomic_write(p, b'two'))
        before = p.stat()
        self.assertFalse(support.atomic_write(p, b'two'))
        self.assertEqual(before, p.stat())
        self.assertEqual(p.stat().st_mode & 0o777, 0o640)
        backups = list(p.parent.glob('config.optimizer-backup-*'))
        self.assertEqual([b.read_bytes() for b in backups], [b'one'])
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

    def test_atomic_preserves_symlink(self):
        target = self.file('target')
        link = self.root / 'link'
        link.symlink_to('target')
        support.atomic_write(link, b'new')
        self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_bytes(), b'new')

    def test_atomic_rejects_dangling_link(self):
        p = self.root / 'link'
        p.symlink_to('absent')
        with self.assertRaises(FileNotFoundError): support.atomic_write(p, b'new')
        self.assertTrue(p.is_symlink())

    def test_atomic_detects_external_edit(self):
        p = self.file('config')
        original = support.signature
        calls = []
        def raced(path):
            calls.append(path)
            if len(calls) == 2: p.write_bytes(b'external')
            return original(path)
        with mock.patch.object(support, 'signature', side_effect=raced):
            with self.assertRaisesRegex(ValueError, 'Concurrent'): support.atomic_write(p, b'new')
        self.assertEqual(p.read_bytes(), b'external')
        self.assertFalse(list(self.root.glob('.optimizer-*')))

    def test_atomic_replace_failure_preserves_original(self):
        p = self.file('config')
        with mock.patch.object(support.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): support.atomic_write(p, b'new')
        self.assertEqual(p.read_bytes(), b'old')
        self.assertFalse(list(self.root.glob('.optimizer-*')))

    def test_rtk_guidance_preserves_content_symlink_and_is_idempotent(self):
        original = b'Personal rules\r\n<!-- ai-memory:start -->keep<!-- ai-memory:end -->\r\n'
        target = self.file('instructions-target', original)
        link = self.root / 'AGENTS.md'
        link.symlink_to(target.name)
        self.assertTrue(support.optimize_rtk_instructions(link))
        self.assertTrue(link.is_symlink())
        self.assertTrue(target.read_bytes().startswith(original))
        self.assertIn(b'rtk test python3 -m unittest', target.read_bytes())
        before = target.stat().st_mtime_ns
        self.assertFalse(support.optimize_rtk_instructions(link))
        self.assertEqual(before, target.stat().st_mtime_ns)
        self.assertEqual(target.read_bytes().count(b'<!-- llm-optimizer:rtk:start -->'), 1)

    def test_rtk_guidance_rejects_malformed_markers(self):
        for content in (b'<!-- llm-optimizer:rtk:start -->',
                        b'<!-- llm-optimizer:rtk:end --><!-- llm-optimizer:rtk:start -->'):
            p = self.file('AGENTS.md', content)
            with self.assertRaises(ValueError): support.optimize_rtk_instructions(p)
            self.assertEqual(p.read_bytes(), content)

    def test_urls(self):
        for value, expected in [('https://example.org/base/mcp/', 'https://example.org/base'),
                                ('http://127.0.0.1:49374/', 'http://127.0.0.1:49374'),
                                ('http://[::1]:49374', 'http://[::1]:49374')]:
            with self.subTest(value=value): self.assertEqual(support.normalize_url(value), expected)
        for value in ['http://remote.test', 'ftp://example.org', 'https://a:b@host',
                      'https://host/?token=x', 'https://host/#secret', 'https://host/../x',
                      'https://host/%2e', 'https://host:bad', 'https:///missing']:
            with self.subTest(value=value):
                with self.assertRaises(ValueError): support.normalize_url(value)

    def test_tags(self):
        for tag in ['v2.0.3', 'v1-rc_1']: self.assertEqual(support.validate_tag(tag), tag)
        for tag in ['', '../x', '-x', 'a/b', 'a\nb', '$(id)']:
            with self.subTest(tag=tag):
                with self.assertRaises(ValueError): support.validate_tag(tag)

    def archive(self, entries):
        archive = self.root / 'asset.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for name, kind in entries:
                info = tarfile.TarInfo(name)
                info.mode = 0o7777
                if kind == 'file':
                    info.size = 4
                    tar.addfile(info, io.BytesIO(b'data'))
                else:
                    info.type = {'dir': tarfile.DIRTYPE, 'link': tarfile.SYMTYPE,
                                 'hard': tarfile.LNKTYPE, 'fifo': tarfile.FIFOTYPE}[kind]
                    info.linkname = '../escape'
                    tar.addfile(info)
        checksum = self.file('checksum', (hashlib.sha256(archive.read_bytes()).hexdigest() + '  asset.tar.gz\n').encode())
        stage = self.root / 'stage'
        stage.mkdir(exist_ok=True)
        return archive, checksum, 'asset.tar.gz', stage

    def test_extract_retains_bundle_and_sanitizes_modes(self):
        args = self.archive([('release/ai-memory', 'file'), ('release/hooks/hook', 'file')])
        self.assertEqual(support.extract_verified(*args, 'ai-memory'), 'release/ai-memory')
        self.assertEqual((args[3] / 'release/hooks/hook').read_bytes(), b'data')
        self.assertEqual((args[3] / 'release/ai-memory').stat().st_mode & 0o7777, 0o755)

    def test_extract_rejects_unsafe_members_before_writes(self):
        for name, kind in [('../escape', 'file'), ('/absolute', 'file'), ('x', 'link'),
                           ('x', 'hard'), ('x', 'fifo')]:
            with self.subTest(name=name, kind=kind):
                args = self.archive([('rtk', 'file'), (name, kind)])
                with self.assertRaises(ValueError): support.extract_verified(*args, 'rtk')
                self.assertEqual(list(args[3].iterdir()), [])

    def test_extract_rejects_checksum_errors(self):
        args = self.archive([('rtk', 'file')])
        correct = args[1].read_text()
        for text in ['', 'bad asset.tar.gz', '0' * 64, correct * 2, correct.replace('asset.tar.gz', 'other')]:
            with self.subTest(text=text):
                args[1].write_text(text)
                with self.assertRaises(ValueError): support.extract_verified(*args, 'rtk')
                self.assertEqual(list(args[3].iterdir()), [])

    def test_extract_rejects_duplicate_members(self):
        args = self.archive([('rtk', 'file'), ('rtk', 'file')])
        with self.assertRaisesRegex(ValueError, 'Duplicate'): support.extract_verified(*args, 'rtk')

    def test_extract_requires_hooks(self):
        args = self.archive([('ai-memory', 'file')])
        with self.assertRaisesRegex(ValueError, 'hooks'): support.extract_verified(*args, 'ai-memory')

    def test_extract_requires_unique_binary(self):
        args = self.archive([('a/rtk', 'file'), ('b/rtk', 'file')])
        with self.assertRaisesRegex(ValueError, 'exactly one'): support.extract_verified(*args, 'rtk')

    def test_publish_preserves_unmanaged_and_updates_managed(self):
        source = self.file('releases/v1/rtk')
        dest = self.file('bin/rtk', b'foreign')
        with self.assertRaises(FileExistsError): support.publish(source, dest, self.root / 'releases')
        self.assertEqual(dest.read_bytes(), b'foreign')
        dest.unlink()
        support.publish(source, dest, self.root / 'releases')
        inode = dest.lstat().st_ino
        support.publish(source, dest, self.root / 'releases')
        self.assertEqual(dest.lstat().st_ino, inode)
        other = self.file('releases/v2/rtk', b'new')
        support.publish(other, dest, self.root / 'releases')
        self.assertEqual(dest.resolve(), other)
        self.assertTrue(source.exists())

    def test_unit_escaping_idempotence_and_foreign_preservation(self):
        p = self.root / 'service'
        args = (p, '/tmp/bin with space', '/tmp/data%$"', '/tmp/config')
        self.assertTrue(support.write_unit(*args))
        self.assertFalse(support.write_unit(*args))
        self.assertIn('data%%$$\\"', p.read_text())
        self.assertIn('--config', p.read_text())
        foreign = self.file('foreign', b'[Service]\n')
        with self.assertRaises(ValueError): support.write_unit(foreign, '/bin/x', '/data', '/config')
        with self.assertRaises(ValueError): support.write_unit(p, '/bin/x\nInjected', '/data', '/config')

    def test_json_validation(self):
        for value in [b'{', b'[]', b'null']:
            p = self.file('config', value)
            with self.assertRaises(ValueError): support.read_json(p)
            self.assertEqual(p.read_bytes(), value)
        self.assertEqual(support.read_json(self.root / 'absent'), {})

    def test_legacy_removes_only_exact_owned_command(self):
        for form, block, agent in [('codex', 'hooks', 'codex'), ('agy', 'rtk-rewrite', 'antigravity')]:
            with self.subTest(form=form):
                owned = {'type': 'command', 'command': '/bin/hook --agent ' + agent}
                foreign = {'type': 'command', 'command': '/bin/foreign'}
                data = {block: {'PreToolUse': [{'matcher': 'Bash', 'hooks': [owned, foreign]}]}, 'keep': 1}
                p = self.file(form, json.dumps(data).encode())
                support.remove_legacy(str(p), form, '/bin/hook')
                result = json.loads(p.read_text())
                self.assertEqual(result[block]['PreToolUse'][0]['hooks'], [foreign])
                self.assertEqual(result['keep'], 1)
                before = p.stat()
                support.remove_legacy(str(p), form, '/bin/hook')
                self.assertEqual(before, p.stat())


class InstallerTests(Fixture):
    def setUp(self):
        super().setUp()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.calls = self.root / 'calls'
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('AI_MEMORY_', 'XDG_', 'CODEX_', 'CLAUDE_'))}
        self.env.update(HOME=str(self.home), PATH=str(self.bin) + ':/usr/bin:/bin',
                        TEST_CALLS=str(self.calls), PYTHONDONTWRITEBYTECODE='1')
        stub = b'''#!/usr/bin/python3
import json, os, sys
with open(os.environ['TEST_CALLS'], 'a') as f: f.write(json.dumps([os.path.basename(sys.argv[0])] + sys.argv[1:]) + '\\n')
failure = os.environ.get('TEST_FAIL', '')
sys.exit(9 if failure and failure in sys.argv[1:] and '--help' not in sys.argv else 0)
'''
        for name in ['codex', 'claude', 'agy', 'ai-memory', 'rtk', 'curl', 'systemctl', 'loginctl']:
            self.file('bin/' + name, stub).chmod(0o755)

    def run_cli(self, *args, env=None):
        return subprocess.run(['/bin/bash', str(ROOT / 'llm-optimizer.sh'), *args],
                              cwd=self.root, env=env or self.env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

    def recorded(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def test_syntax(self):
        self.assertEqual(subprocess.run(['bash', '-n', str(ROOT / 'llm-optimizer.sh')]).returncode, 0)
        compile((ROOT / 'optimizer_support.py').read_text(), 'optimizer_support.py', 'exec')

    def test_help_and_noop(self):
        for args in [('--help',), ('--skip-rtk', '--skip-ai-memory')]:
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(self.recorded(), [])

    def test_invalid_args_before_writes(self):
        for args in [('--wat',), ('--agents',), ('--service', 'bad'), ('--agents', 'unknown'),
                     ('--agents', 'codex,'), ('--project-strategy', 'bad')]:
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(list(self.home.iterdir()), [])

    def test_dry_run_no_writes_or_agent_execution(self):
        result = self.run_cli('--dry-run', '--agents', 'all')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(self.recorded(), [])

    def test_debug_log_is_private_append_only_and_records_failures(self):
        self.env['TEST_FAIL'] = 'init'
        result = self.run_cli('--agents', 'codex', '--skip-ai-memory')
        self.assertNotEqual(result.returncode, 0)
        path = self.root / 'llm-optimizer.log'
        before = path.read_bytes()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIn(b'DONE: RTK official Codex instructions (status 9,', before)
        self.assertIn(b'END llm-optimizer status=1', before)
        self.assertRegex(before.decode(), r'\[\d{4}-\d{2}-\d{2}T')
        result = self.run_cli('--dry-run', '--agents', 'codex')
        self.assertEqual(result.returncode, 0, result.stderr)
        after = path.read_bytes()
        self.assertTrue(after.startswith(before))
        self.assertEqual(after.count(b'=== START'), 2)
        self.assertIn(b'END llm-optimizer status=0', after)

    def test_debug_log_refuses_symlinks_and_nonregular_files(self):
        path = self.root / 'llm-optimizer.log'
        precious = self.file('precious', b'keep')
        path.symlink_to(precious)
        self.assertNotEqual(self.run_cli('--agents', 'codex').returncode, 0)
        self.assertEqual(precious.read_bytes(), b'keep')
        self.assertEqual(self.recorded(), [])
        path.unlink()
        path.mkdir()
        self.assertNotEqual(self.run_cli('--agents', 'codex').returncode, 0)
        self.assertEqual(self.recorded(), [])

    def test_debug_log_flushes_partial_command_output_before_completion(self):
        gate = self.root / 'release-command'
        self.env['TEST_GATE'] = str(gate)
        self.file('bin/rtk', b'''#!/usr/bin/python3
import os, sys, time
if 'init' in sys.argv and '--help' not in sys.argv:
    sys.stderr.write('diagnostic without newline')
    sys.stderr.flush()
    while not os.path.exists(os.environ['TEST_GATE']): time.sleep(0.02)
''').chmod(0o755)
        process = subprocess.Popen(['bash', str(ROOT / 'llm-optimizer.sh'),
                                    '--agents', 'codex', '--skip-ai-memory'],
                                   cwd=self.root, env=self.env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        path = self.root / 'llm-optimizer.log'
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if path.exists() and 'diagnostic without newline' in path.read_text():
                    break
                time.sleep(0.02)
            else: self.fail('Command output was not logged while command was running')
            self.assertIsNone(process.poll())
            gate.touch()
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            self.assertIn('END llm-optimizer status=0', path.read_text())
            self.assertNotIn('diagnostic without newline', stdout + stderr)
        finally:
            gate.touch()
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)

    def test_capability_help_and_install_details_are_log_only(self):
        self.file('bin/rtk', b'''#!/usr/bin/python3
import sys
print('Usage: rtk [OPTIONS]' if '--help' in sys.argv else 'Internal installation diagnostic')
print('provider diagnostic on stderr', file=sys.stderr)
''').chmod(0o755)
        result = self.run_cli('--agents', 'codex', '--skip-ai-memory')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        details = (self.root / 'llm-optimizer.log').read_text()
        for message in ('Usage: rtk [OPTIONS]', 'Internal installation diagnostic',
                        'provider diagnostic on stderr'):
            self.assertIn(message, details)
            self.assertNotIn(message, result.stdout + result.stderr)
        self.assertIn('RUN: RTK official Codex instructions', result.stdout)
        self.assertIn('DONE: RTK official Codex instructions (status 0,', result.stdout)
        self.assertIn('Errors: 0', result.stdout)

    def test_report_exclusive_and_private(self):
        p = self.file('report', b'precious')
        result = self.run_cli('--dry-run', '--report', str(p))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(p.read_bytes(), b'precious')
        for target in ['report', 'absent']:
            link = self.root / ('link-' + target)
            link.symlink_to(target)
            self.assertNotEqual(self.run_cli('--dry-run', '--report', str(link)).returncode, 0)
            self.assertTrue(link.is_symlink())
        new = self.root / 'new-report'
        self.assertEqual(self.run_cli('--dry-run', '--report', str(new)).returncode, 0)
        self.assertEqual(new.stat().st_mode & 0o777, 0o600)

    def test_report_write_error_fails(self):
        result = self.run_cli('--dry-run', '--report', str(self.root / 'missing/report'))
        self.assertNotEqual(result.returncode, 0)

    def test_invalid_config_preserved(self):
        p = self.file('home/.codex/hooks.json', b'{broken')
        result = self.run_cli('--agents', 'codex')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(p.read_bytes(), b'{broken')
        self.assertEqual(self.recorded(), [])

    def test_all_official_integrations_and_base_path(self):
        result = self.run_cli('--agents', 'all', '--server-url', 'https://example.org/base/mcp', '--project-strategy', 'repo-root')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.recorded()
        hooks = [c for c in calls if 'install-hooks' in c and '--help' not in c]
        mcps = [c for c in calls if 'install-mcp' in c and '--help' not in c]
        self.assertEqual(len(hooks), 3)
        self.assertEqual(len(mcps), 3)
        for call in hooks:
            self.assertIn('https://example.org/base', call)
            self.assertIn('repo-root', call)
        for call in mcps: self.assertIn('https://example.org/base/mcp', call)
        self.assertIn(['rtk', 'init', '-g', '--codex'], calls)
        self.assertIn(['rtk', 'init', '-g', '--auto-patch'], calls)
        self.assertIn(['rtk', 'init', '--agent', 'antigravity'], calls)
        self.assertFalse(any(c[0] in ('systemctl', 'loginctl') for c in calls))

    def test_failed_server_blocks_memory_but_runs_rtk(self):
        self.env['TEST_FAIL'] = 'status'
        result = self.run_cli('--agents', 'codex', '--server-url', 'https://example.org')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('install-mcp' in c and '--help' not in c for c in self.recorded()))
        self.assertIn(['rtk', 'init', '-g', '--codex'], self.recorded())

    def test_failed_mcp_blocks_hooks_and_routing(self):
        self.env['TEST_FAIL'] = 'install-mcp'
        result = self.run_cli('--agents', 'codex', '--server-url', 'https://example.org', '--skip-rtk')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('install-hooks' in c and '--help' not in c for c in self.recorded()))
        self.assertFalse(any('install-instructions' in c and '--help' not in c for c in self.recorded()))

    def test_hidden_prompt_cannot_consume_terminal_input(self):
        self.file('bin/rtk', b'''#!/usr/bin/python3
import json, os, sys
if 'init' in sys.argv and '--help' not in sys.argv:
    assert os.path.isdir(os.path.join(os.environ['HOME'], '.claude'))
    print('Enable anonymous telemetry? [y/N] ', end='', flush=True)
    with open(os.environ['TEST_CALLS'], 'a') as f:
        f.write(json.dumps(['prompt-input', sys.stdin.readline()]) + '\\n')
''').chmod(0o755)
        result = subprocess.run(['bash', str(ROOT / 'llm-optimizer.sh'),
                                 '--agents', 'claude', '--skip-ai-memory'],
                                cwd=self.root, env=self.env, input='y\n',
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(['prompt-input', ''], self.recorded())

    def test_rtk_guidance_is_installed_for_all_agents(self):
        result = self.run_cli('--agents', 'all', '--skip-ai-memory')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for p in (self.home / '.claude/CLAUDE.md', self.home / '.codex/AGENTS.md',
                  self.root / 'AGENTS.md'):
            self.assertIn('rtk test python3 -m unittest', p.read_text())
            self.assertIn('rtk proxy COMMAND', p.read_text())
        result = self.run_cli('--agents', 'all', '--skip-ai-memory')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / 'AGENTS.md').read_text().count(
            '<!-- llm-optimizer:rtk:start -->'), 1)

    def test_rtk_failure_is_reported(self):
        self.env['TEST_FAIL'] = 'init'
        result = self.run_cli('--agents', 'codex', '--skip-ai-memory')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('official integration applied', result.stdout)

    def test_wrapper_rejected_for_local_service(self):
        result = self.run_cli('--agents', 'codex', '--skip-rtk')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('native ELF', result.stdout)

    def test_rtk_only_ignores_memory_server_environment(self):
        self.env['AI_MEMORY_SERVER_URL'] = 'http://remote.invalid'
        result = self.run_cli('--agents', 'codex', '--skip-ai-memory')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(['rtk', 'init', '-g', '--codex'], self.recorded())

    def test_reused_binaries_do_not_require_curl(self):
        # Source functions to emulate a host without curl while keeping core tools.
        script = '''source "$1"
ensure_curl() { error 'Unexpected curl dependency'; return 1; }
main --agents codex --server-url https://example.org
'''
        result = subprocess.run(['bash', '-c', script, 'test', str(ROOT / 'llm-optimizer.sh')],
                                cwd=self.root, env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_project_routing_snapshot_includes_custom_symlink(self):
        target = self.file('instructions-original', b'project rules')
        link = self.root / 'custom instructions.md'
        link.symlink_to(target.name)
        result = self.run_cli('--agents', 'codex', '--skip-rtk',
                              '--server-url', 'https://example.org', '--routing-target', str(link))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifests = list(self.home.glob('.local/state/llm-optimizer/backup-*/manifest.json'))
        records = json.loads(manifests[0].read_text())
        record = next(r for r in records if r['path'] == str(link))
        self.assertEqual(record['symlink'], target.name)
        self.assertEqual(Path(record['backup']).read_bytes(), b'project rules')
        self.assertEqual(Path(record['backup']).stat().st_mode & 0o777, 0o600)

    def test_rtk_project_snapshot(self):
        self.file('.agents/rules/antigravity-rtk-rules.md', b'old instructions')
        result = self.run_cli('--agents', 'agy', '--skip-ai-memory')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = next(self.home.glob('.local/state/llm-optimizer/backup-*/manifest.json'))
        records = {r['path']: r for r in json.loads(manifest.read_text())}
        for name in ('.agents/rules/antigravity-rtk-rules.md',):
            self.assertIn(str(self.root / name), records)

    def release_fixture(self, corrupt=False):
        # Exercise fetch -> checksum -> full staging -> validation -> publish -> init.
        asset = 'rtk-x86_64-unknown-linux-musl.tar.gz'
        archive = self.root / asset
        content = (self.bin / 'rtk').read_bytes()
        with tarfile.open(archive, 'w:gz') as tar:
            member = tarfile.TarInfo('release/rtk')
            member.mode = 0o755
            member.size = len(content)
            tar.addfile(member, io.BytesIO(content))
        digest = '0' * 64 if corrupt else hashlib.sha256(archive.read_bytes()).hexdigest()
        self.file('checksums.txt', (digest + '  ' + asset + '\n').encode())
        self.file('release.json', b'{"tag_name":"v-test"}')
        self.env['TEST_RELEASE_DIR'] = str(self.root)
        self.file('bin/uname', b'#!/bin/sh\ncase "$1" in -m) echo x86_64;; *) echo Linux;; esac\n').chmod(0o755)
        self.file('bin/curl', b'''#!/usr/bin/python3
import json, os, pathlib, shutil, sys
with open(os.environ['TEST_CALLS'], 'a') as f: f.write(json.dumps(['curl'] + sys.argv[1:]) + '\\n')
url = sys.argv[sys.argv.index('-o') - 1]
name = 'release.json' if url.endswith('/latest') else url.rsplit('/', 1)[1]
shutil.copyfile(pathlib.Path(os.environ['TEST_RELEASE_DIR']) / name, sys.argv[sys.argv.index('-o') + 1])
''').chmod(0o755)
        (self.bin / 'rtk').unlink()

    def test_verified_release_install_and_repeat(self):
        self.release_fixture()
        for _ in range(2):
            result = self.run_cli('--agents', 'codex', '--skip-ai-memory')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        published = self.home / '.local/bin/rtk'
        self.assertTrue(published.is_symlink())
        self.assertEqual(published.resolve().parent.name, 'release')
        self.assertIn('official integration applied', result.stdout)
        self.assertFalse(list(self.home.glob('.local/share/llm-optimizer/releases/.stage.*')))

    def test_bad_release_blocks_publication_and_integration(self):
        self.release_fixture(corrupt=True)
        result = self.run_cli('--agents', 'codex', '--skip-ai-memory')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / '.local/bin/rtk').exists())
        self.assertFalse(any(c[0] == 'rtk' for c in self.recorded()))
        self.assertFalse(list(self.home.glob('.local/share/llm-optimizer/releases/.stage.*')))

    def test_managed_service_start_and_idempotent_rerun(self):
        # A native no-op fixture satisfies ELF checks; systemctl is still simulated.
        (self.bin / 'ai-memory').unlink()
        (self.bin / 'ai-memory').symlink_to('/bin/true')
        self.file('bin/curl', b'#!/bin/sh\nexit 7\n').chmod(0o755)
        for key in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'LLM_API_KEY'):
            self.env.pop(key, None)
        result = self.run_cli('--agents', 'codex', '--skip-rtk', '--enable-linger')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        unit = self.home / '.config/systemd/user/ai-memory.service'
        before = unit.stat()
        self.assertIn(str(self.home / '.local/share/ai-memory/config.toml'), unit.read_text())
        self.assertIn(['systemctl', '--user', 'restart', 'ai-memory.service'], self.recorded())
        self.assertTrue(any(c[0] == 'loginctl' for c in self.recorded()))
        self.calls.unlink()
        result = self.run_cli('--agents', 'codex', '--skip-rtk')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((before.st_ino, before.st_mtime_ns, before.st_size),
                         (unit.stat().st_ino, unit.stat().st_mtime_ns, unit.stat().st_size))
        self.assertIn(['systemctl', '--user', 'start', 'ai-memory.service'], self.recorded())
        self.assertNotIn(['systemctl', '--user', 'restart', 'ai-memory.service'], self.recorded())

    def test_no_user_variable_required(self):
        self.env.pop('USER', None)
        self.assertEqual(self.run_cli('--dry-run').returncode, 0)

    def test_existing_lock_blocks_installers(self):
        import fcntl
        lock = self.file('home/.local/state/llm-optimizer/install.lock', b'')
        with lock.open('w') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_cli('--agents', 'codex', '--skip-ai-memory')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.recorded(), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
