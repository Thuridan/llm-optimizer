#!/usr/bin/env python3
"""Filesystem operations for llm-optimizer. No agent or shell execution."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import sys
import tarfile
import tempfile
import datetime
import fcntl
import signal
import selectors
import subprocess
from urllib.parse import urlsplit, urlunsplit


def logged_run(path, command):
    """Append a private live transcript, including partial lines and final status."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'wb', buffering=0) as transcript:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError('Debug log must be an owned regular file without hard links')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.fchmod(fd, 0o600)
        def record(content):
            stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')
            pending = memoryview(('[' + stamp + '] ').encode() + content +
                                 (b'' if content.endswith(b'\n') else b'\n'))
            while pending:
                written = transcript.write(pending)
                if not written:
                    raise OSError('Debug log write failed')
                pending = pending[written:]
        record(b'=== START llm-optimizer ===')
        detail_read, detail_write = os.pipe()
        try:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                pass_fds=(detail_write,),
                env=dict(os.environ, LLM_OPTIMIZER_DETAIL_FD=str(detail_write)))
        except BaseException:
            os.close(detail_read)
            raise
        finally:
            os.close(detail_write)
        streams = selectors.DefaultSelector()
        streams.register(process.stdout, selectors.EVENT_READ, True)
        streams.register(detail_read, selectors.EVENT_READ, False)
        previous = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda number, frame: process.send_signal(number))
        try:
            while streams.get_map():
                for stream, _ in streams.select():
                    chunk = os.read(stream.fd, 8192)
                    if not chunk:
                        streams.unregister(stream.fileobj)
                        continue
                    # Both streams are logged live, including partial diagnostic lines.
                    for line in chunk.splitlines(keepends=True):
                        record(line)
                    if stream.data:
                        try:
                            sys.stdout.buffer.write(chunk)
                            sys.stdout.buffer.flush()
                        except BrokenPipeError:
                            pass  # Preserve the log if the terminal viewer disconnects.
            status = process.wait()
            status = status if status >= 0 else 128 - status
            record(('=== END llm-optimizer status=' + str(status) + ' ===\n').encode())
            return status
        finally:
            streams.close()
            os.close(detail_read)
            process.stdout.close()
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            if process.poll() is None:
                process.kill()
                process.wait()


def signature(path):
    if not path.exists():
        return None
    st = path.stat()
    return st.st_dev, st.st_ino, st.st_mtime_ns, path.read_bytes()


RTK_GUIDANCE = """<!-- llm-optimizer:rtk:start -->
## Efficient command output

Use supported RTK commands for exploratory shell output: `rtk git status`,
`rtk git diff --stat`, `rtk git log -5 --oneline`, `rtk rg PATTERN PATH`,
and the dedicated test/build/lint filter for the tool in use.
For Python unittest use `rtk test python3 -m unittest discover -s tests`;
other unsupported test runners can use `rtk test COMMAND ...`.
Check exit status; filtered output alone cannot establish success.

Narrow paths, patterns, and revisions before reading. Use `rg --files` for
filename discovery, `rtk read --level minimal FILE` for exploration, and
`rtk read --level aggressive FILE` only for a structural overview.
Read exact relevant lines before editing; aggressive reads omit implementation.
Use `rtk proxy COMMAND ...` for machine-readable data, exact diffs, or raw output.
For failures inspect the saved full-output file before rerunning commands;
saved output can be truncated. Do not blindly prefix shell syntax with RTK.

Keep detailed installer logs on disk; read the failing phase instead of the
whole log. Avoid repeated unchanged reads and already-passing tests.
Use `rtk gain --weekly` and `rtk discover` for occasional audits, not every turn.
<!-- llm-optimizer:rtk:end -->
"""


def optimize_rtk_instructions(path):
    path = Path(path)
    # Work in bytes so unrelated instructions retain their exact line endings.
    content = path.read_bytes() if path.exists() else b''
    start = b'<!-- llm-optimizer:rtk:start -->'
    end = b'<!-- llm-optimizer:rtk:end -->'
    block = RTK_GUIDANCE.encode()
    if start in content or end in content:
        if content.count(start) != 1 or content.count(end) != 1:
            raise ValueError('Ambiguous RTK instruction markers; file preserved')
        left, right = content.index(start), content.index(end)
        if right < left:
            raise ValueError('Reversed RTK instruction markers; file preserved')
        right += len(end)
        result = content[:left] + block.rstrip(b'\n') + content[right:]
    else:
        result = content + (b'\n\n' if content else b'') + block
    return atomic_write(path, result)


def atomic_write(path, content):
    """Preserve symlink targets, back up old bytes, and reject observed conflicts.

    The installer lock serializes our writers. The final compare catches external
    edits before publication, but cannot lock an uncooperative external editor.
    """
    visible = Path(path).absolute()
    link = os.readlink(visible) if visible.is_symlink() else None
    path = visible.resolve(strict=True) if link is not None else visible
    before = signature(path)
    if before is not None and before[-1] == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if before is not None:
        fd, backup = tempfile.mkstemp(prefix=visible.name + '.optimizer-backup-', dir=visible.parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(before[-1]); stream.flush(); os.fsync(stream.fileno())
        print('Backup: ' + backup, file=sys.stderr)
    fd, temporary = tempfile.mkstemp(prefix='.optimizer-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            if before is not None:
                os.fchmod(stream.fileno(), stat.S_IMODE(path.stat().st_mode))
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        if link is not None and (not visible.is_symlink() or os.readlink(visible) != link):
            raise ValueError('Symlink changed during update')
        if link is None and visible.is_symlink():
            raise ValueError('Destination became a symlink')
        if signature(path) != before:
            raise ValueError('Concurrent edit detected; destination preserved')
        if before is None:
            os.link(temporary, path)  # Exclusive creation, including dangling links.
        else:
            os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def normalize_url(value):
    url = urlsplit(value)
    if (url.scheme not in ('http', 'https') or not url.hostname or url.username
            or url.password or url.query or url.fragment):
        raise ValueError('Use an HTTP(S) URL without credentials, query or fragment')
    _ = url.port
    path = url.path.rstrip('/')
    if path.endswith('/mcp'):
        path = path[:-4]
    if any(p in ('.', '..') for p in path.split('/')) or re.search(r'[^A-Za-z0-9/._~-]', path):
        raise ValueError('Invalid server base path')
    if url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Remote server URLs require HTTPS')
    return urlunsplit((url.scheme, url.netloc, path, '', ''))


def validate_tag(tag):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', tag):
        raise ValueError('Invalid release tag')
    return tag


def extract_verified(archive, checksum_file, asset, directory, binary):
    hashes = []
    for line in Path(checksum_file).read_text().splitlines():
        fields = line.split()
        if len(fields) == 1 and re.fullmatch(r'[A-Fa-f0-9]{64}', fields[0]):
            hashes.append(fields[0].lower())
        elif len(fields) == 2 and fields[1].lstrip('*') in (asset, './' + asset):
            if not re.fullmatch(r'[A-Fa-f0-9]{64}', fields[0]):
                raise ValueError('Malformed checksum')
            hashes.append(fields[0].lower())
    if len(hashes) != 1:
        raise ValueError('Missing or ambiguous checksum')
    digest = hashlib.sha256()
    with open(archive, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != hashes[0]:
        raise ValueError('Checksum mismatch')
    root = Path(directory).resolve()
    if not root.is_dir() or any(root.iterdir()):
        raise ValueError('Extraction requires an empty staging directory')
    with tarfile.open(archive, 'r:gz') as tar:
        members, total = [], 0
        for member in tar:
            members.append(member)
            total += member.size
            if len(members) > 20000 or total > 1024**3:
                raise ValueError('Release exceeds extraction bounds')
        seen = set()
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe archive member')
            if path in seen:
                raise ValueError('Duplicate archive member')
            seen.add(path)
        # Deliberately do not extract symlinks, hardlinks, devices or archive ownership.
        for member in members:
            dest = root / member.name
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, open(dest, 'xb') as out:
                    shutil.copyfileobj(source, out)
                dest.chmod(0o755 if member.mode & 0o111 else 0o644)
    found = [p for p in root.rglob(binary) if p.is_file()]
    if len(found) != 1:
        raise ValueError('Expected exactly one release executable')
    found[0].chmod(0o755)
    if binary == 'ai-memory' and not (found[0].parent / 'hooks').is_dir():
        raise ValueError('ai-memory release lacks sibling hooks bundle')
    return str(found[0].relative_to(root))


def publish(source, destination, release_root):
    source, destination, release_root = map(Path, (source, destination, release_root))
    if os.path.lexists(destination):
        if not destination.is_symlink() or release_root.resolve() not in destination.resolve().parents:
            raise FileExistsError('Unmanaged executable preserved: ' + str(destination))
        if destination.resolve() == source.resolve():
            return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.optimizer-link-', dir=destination.parent) as temporary:
        link = Path(temporary) / 'binary'
        link.symlink_to(source)
        os.replace(link, destination)


def write_unit(path, binary, data, config):
    marker = '# Managed by llm-optimizer v2\n'
    path = Path(path)
    if path.exists() and not path.read_text().startswith(marker):
        raise ValueError('Refusing to replace unmanaged service')
    def quote(value):
        if any(c in value for c in '\n\r\x00'):
            raise ValueError('Invalid unit argument')
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'
    argv = [binary, '--data-dir', data, '--config', config, 'serve', '--transport',
            'http', '--bind', '127.0.0.1:49374', '--enable-web']
    text = (marker + '[Unit]\nDescription=AI Memory local server\nAfter=network.target\n\n'
            '[Service]\nType=simple\nExecStart=' + ' '.join(map(quote, argv)) +
            '\nRestart=on-failure\nRestartSec=5\nUMask=0077\n\n[Install]\nWantedBy=default.target\n')
    return atomic_write(path, text.encode())


def read_json(path):
    path = Path(path)
    if path.is_symlink():
        path.resolve(strict=True)
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError('Configuration must be a JSON object')
    return data


def remove_legacy(path, form, hook):
    data = read_json(path)
    block = data.get('hooks' if form == 'codex' else 'rtk-rewrite', {})
    if not isinstance(block, dict):
        raise ValueError('Invalid hook block')
    groups = block.get('PreToolUse', [])
    if not isinstance(groups, list):
        raise ValueError('Invalid hook groups')
    retained = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get('hooks', []), list):
            raise ValueError('Invalid hook group')
        hooks = []
        for entry in group.get('hooks', []):
            if not isinstance(entry, dict):
                raise ValueError('Invalid hook entry')
            command = entry.get('command')
            try:
                parts = shlex.split(command) if isinstance(command, str) else []
            except ValueError:
                parts = []
            expected = [hook, '--agent', 'codex' if form == 'codex' else 'antigravity']
            if entry.get('type') != 'command' or parts != expected:
                hooks.append(entry)
        if hooks or not group.get('hooks'):
            retained.append(dict(group, hooks=hooks))
    if retained != groups:
        block['PreToolUse'] = retained
        atomic_write(path, (json.dumps(data, indent=2) + '\n').encode())
        print('Removed exact v1 custom rewrite registration: ' + path)


def snapshot(state, agents, extra_paths=()):
    home = Path.home()
    claude = Path(os.environ.get('CLAUDE_CONFIG_DIR', home / '.claude'))
    codex = Path(os.environ.get('CODEX_HOME', home / '.codex'))
    paths = []
    if 'claude' in agents:
        paths += [claude / 'settings.json', claude / 'CLAUDE.md', claude / 'RTK.md',
                  claude / '.claude.json' if os.environ.get('CLAUDE_CONFIG_DIR') else home / '.claude.json']
    if 'codex' in agents:
        paths += [codex / 'hooks.json', codex / 'config.toml', codex / 'AGENTS.md', codex / 'RTK.md']
    if 'antigravity' in agents:
        paths += [home / '.gemini/config/hooks.json', home / '.gemini/antigravity-cli/settings.json']
    paths += [Path(p).absolute() for p in extra_paths]
    paths = list(dict.fromkeys(paths))
    if not paths:
        return
    root = Path(tempfile.mkdtemp(prefix='backup-', dir=state))
    records = []
    for i, path in enumerate(paths):
        record = {'path': str(path), 'existed': path.exists(), 'symlink': os.readlink(path) if path.is_symlink() else None}
        if path.exists():
            content = path.read_bytes()
            backup = root / str(i)
            with open(backup, 'xb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(content)
            record.update(backup=str(backup), sha256=hashlib.sha256(content).hexdigest(), mode=stat.S_IMODE(path.stat().st_mode))
        records.append(record)
    (root / 'manifest.json').write_text(json.dumps(records, indent=2) + '\n')
    print('Configuration recovery snapshot: ' + str(root))


def main(argv):
    mode, *args = argv
    if mode == 'logged-run':
        sys.exit(logged_run(args[0], args[1:]))
    elif mode == 'path':
        if any(c in args[0] for c in '\n\r\x00'):
            raise ValueError('Invalid path')
        print(os.path.abspath(os.path.expanduser(args[0])))
    elif mode == 'url': print(normalize_url(args[0]))
    elif mode == 'tag': print(validate_tag(args[0]))
    elif mode == 'release-tag': print(validate_tag(json.loads(Path(args[0]).read_text())['tag_name']))
    elif mode == 'extract': print(extract_verified(*args))
    elif mode == 'publish': publish(*args)
    elif mode == 'unit': print('changed' if write_unit(*args) else 'unchanged')
    elif mode == 'rtk-instructions':
        print('changed' if optimize_rtk_instructions(args[0]) else 'unchanged')
    elif mode == 'legacy': remove_legacy(*args)
    elif mode == 'json-check':
        for path in args: read_json(path)
    elif mode == 'snapshot':
        split = args.index('--') if '--' in args else len(args)
        snapshot(args[0], args[1:split], args[split + 1:])
    elif mode == 'native':
        with open(args[0], 'rb') as stream:
            if stream.read(4) != b'\x7fELF': raise ValueError('Not a native Linux executable')
    else: raise ValueError('Unknown operation')


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception as exc:
        # JSON decoder errors and filesystem errors do not include file contents.
        print('optimizer: ' + type(exc).__name__ + ': ' + str(exc)[:200], file=sys.stderr)
        sys.exit(1)
