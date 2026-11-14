"""Minimal main plus disposable browser owner. Never log credentials, page data or ticket URLs."""
import asyncio
import json
import os
import pathlib
import re
import secrets
import time
import hashlib
import fcntl
from contextlib import asynccontextmanager


class OperationError(ValueError):
    """Safe, static operator-facing errors; never contains page/input data."""


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    import tempfile
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    temp = pathlib.Path(name)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


class Session:
    def __init__(self, manager, kind):
        self.manager = manager
        self.root = manager.root
        self.kind = kind
        self.lock = asyncio.Lock()
        self.connections = set()
        self.pages = {}
        self.active = None
        self.history = manager.history
        self.display_process = self.wm_process = None
        self.display_dir = None
        self.xauthority = os.environ.get("XAUTHORITY", "/home/kasm-user/.Xauthority")
        self.port = 6901 if kind == 'main' else 6902
        self.display = ':1' if kind == 'main' else ':2'

    def session(self, sid):
        if not self.active or self.active['session_id'] != sid:
            raise OperationError('unknown or closed session')
        return self.active

    def save(self):
        self.manager.save()

    def info(self):
        return dict(self.active, page_ids=[pid for pid,p in self.pages.items() if not p.is_closed()]) if self.active else None

    async def start_display(self):
        if self.kind == 'main':
            return
        import subprocess, tempfile
        if pathlib.Path('/tmp/.X11-unix/X2').exists():
            raise OperationError('temporary display already occupied')
        self.display_dir = tempfile.TemporaryDirectory(prefix='display-', dir=self.root)
        self.xauthority = str(pathlib.Path(self.display_dir.name) / 'Xauthority')
        pathlib.Path(self.xauthority).touch(mode=0o600)
        subprocess.run(['xauth', '-f', self.xauthority],
            input='add :2 . ' + secrets.token_hex(16) + '\n', text=True, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Fixed internal endpoint, same installed Kasm server and private auth file.
        self.display_process = subprocess.Popen([
            '/usr/bin/Xvnc', ':2', '-geometry', '1024x768', '-depth', '24',
            '-interface', '127.0.0.1', '-websocketPort', '6902', '-rfbport', '5902',
            '-httpd', '/usr/share/kasmvnc/www', '-sslOnly',
            '-cert', '/home/kasm-user/.vnc/self.pem', '-key', '/home/kasm-user/.vnc/self.pem',
            '-KasmPasswordFile', '/home/kasm-user/.kasmpasswd',
            '-rfbauth', '/home/kasm-user/.vnc/passwd', '-nolisten', 'tcp',
            '-auth', self.xauthority, '-AcceptSetDesktopSize', '0',
            '-udpPort', '0', '-PublicIP', '127.0.0.1', '-Log', '*:stderr:0'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if self.display_process.poll() is not None:
                raise OperationError('temporary display startup failed')
            if pathlib.Path('/tmp/.X11-unix/X2').exists():
                break
            await asyncio.sleep(.05)
        else:
            raise OperationError('temporary display startup timed out')
        self.wm_process = subprocess.Popen(['/usr/bin/xfwm4', '--replace', '--sm-client-disable'],
            env=dict(os.environ, DISPLAY=self.display, XAUTHORITY=self.xauthority), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    async def stop_display(self):
        for process in (self.wm_process, self.display_process):
            if process and process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, 5)
                except Exception:
                    process.kill()
                    await asyncio.to_thread(process.wait)

        if self.display_dir:
            self.display_dir.cleanup()
            self.display_dir = None

    async def launch(self, path=None, state=None):
        from camoufox.async_api import AsyncCamoufox
        from camoufox.utils import launch_options
        try:
            await self.start_display()
            identity = path / 'identity.json' if path else None
            if identity and identity.exists():
                options = json.loads(identity.read_text())
            else:
                options = await asyncio.to_thread(launch_options, headless=False, os='linux', canvas_noise=True,
                                                 pin_cpu_cores=False, window=(1000, 700))
                options['env'] = {k:v for k,v in options.get('env', {}).items() if k.startswith('CAMOU_CONFIG')}
                options = json.loads(json.dumps(options, default=str))
                if identity:
                    atomic(identity, options)
            options = dict(options)
            options['env'] = dict(os.environ, **options.get('env', {}), DISPLAY=self.display, XAUTHORITY=self.xauthority)
            if path:
                options['user_data_dir'] = str(path / 'native')
            self.cm = AsyncCamoufox(from_options=options, persistent_context=bool(path))
            launched = await self.cm.__aenter__()
            self.context = launched if path else await launched.new_context(
                storage_state=state, viewport={'width':1000,'height':700})
            self.context.set_default_timeout(12000)
            for page in self.context.pages:
                self.pages[secrets.token_hex(12)] = page
            if not self.pages:
                self.pages[secrets.token_hex(12)] = await self.context.new_page()
            self.context.on('close', self.crashed)
        except BaseException:
            if hasattr(self, 'cm'):
                try: await self.cm.__aexit__(None, None, None)
                except Exception: pass
            await self.stop_display()
            raise OperationError('browser or isolated display startup failed') from None

    def crashed(self, *args):
        if self.active and self.active['state'] not in ('closed', 'interrupted'):
            self.active['state'] = 'interrupted'
            self.save()
            asyncio.create_task(self.disconnect())
            if self.kind == 'temporary':
                asyncio.create_task(self.stop_display())

    @asynccontextmanager
    async def automation(self, sid):
        async with self.lock:
            s = self.session(sid)
            if s['state'] not in ('automation', 'returned'):
                raise OperationError('automation paused: ' + s['state'])
            yield s

    def page(self, pid):
        page = self.pages.get(pid)
        if not page or page.is_closed():
            raise OperationError('unknown or closed page')
        return page

    async def close(self, sid):
        async with self.lock:
            s = self.session(sid)
            if s['state'] not in ('automation', 'returned', 'interrupted'):
                raise OperationError('automation paused: ' + s['state'])
            self.active['state'] = 'closed'
            self.save()
            try:
                await self.cm.__aexit__(None, None, None)
            except Exception:
                self.active['state'] = 'interrupted'
                self.save()
                raise OperationError('browser close interrupted') from None
            self.history.append(self.active)
            self.active = None
            await self.stop_display()
            self.manager.sessions.pop(sid, None)
            self.manager.save()
            return {'state':'closed'}

    async def action(self, sid, pid, action, **args):
        async with self.automation(sid):
            p = self.page(pid)
            if action == 'navigate':
                url = args['url']
                from urllib.parse import urlsplit
                if url != 'about:blank' and urlsplit(url).scheme not in ('http', 'https'):
                    raise OperationError('only HTTP(S) navigation allowed')
                await p.goto(url, wait_until='domcontentloaded')
            elif action == 'click':
                await p.locator(args['selector']).click()
            elif action == 'type':
                await p.locator(args['selector']).fill(args['text'])
            elif action == 'scroll':
                await p.mouse.wheel(0, max(-4000, min(4000, args.get('pixels', 600))))
            elif action == 'snapshot':
                return (await p.locator('body').inner_text())[:20000]
            else:
                raise OperationError('unknown action')
            return {'ok':True}

    async def disconnect(self):
        # Close upstream input sockets before releasing automation ownership.
        for ws in list(self.connections):
            await ws.close()
        self.connections.clear()

    async def handoff(self, sid, ttl=300):
        async with self.lock:
            if (self.root / 'maintenance.lock').exists():
                raise OperationError('maintenance in progress')
            s = self.session(sid)
            if s['state'] in ('closed', 'interrupted'):
                raise OperationError('session unavailable')
            await self.disconnect()
            token = secrets.token_urlsafe(32)
            self.ticket_hash = hashlib.sha256(token.encode()).hexdigest()
            self.claim_id = None
            self.expiry = time.time() + max(1, min(900, ttl))
            s['state'] = 'pending'
            self.save()
            return {'ticket':token, 'expires_at':self.expiry, 'session_id':sid}

    def claim(self, token):
        if not isinstance(token, str) or len(token) > 128:
            raise OperationError('invalid or expired ticket')
        digest = hashlib.sha256(token.encode()).hexdigest()
        if (not self.active or self.active['state'] != 'pending' or
            time.time() >= getattr(self, 'expiry', 0) or
            not secrets.compare_digest(digest, getattr(self, 'ticket_hash', ''))):
            raise OperationError('invalid or expired ticket')
        self.ticket_hash = ''
        self.claim_id = secrets.token_urlsafe(32)
        self.active['state'] = 'human'
        self.save()
        return self.claim_id

    def authorized(self, claim):
        return bool(self.active and self.active['state'] == 'human' and claim and
                    time.time() < getattr(self, 'expiry', 0) and
                    secrets.compare_digest(claim, getattr(self, 'claim_id', '') or ''))

    async def handback(self, claim):
        async with self.lock:
            if not self.authorized(claim):
                raise OperationError('invalid or expired claim')
            await self.disconnect()
            self.claim_id = None
            self.active['state'] = 'returned'
            self.save()
            return {'state':'returned'}


class Owner:
    """Two-session lifecycle, no global lock during browser operations or handoff."""
    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.process_lock = open(self.root / 'owner.lock', 'a')
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.process_lock.close()
            raise RuntimeError('another browser owner is running') from None
        self.lock = asyncio.Lock()
        self.sessions = {}
        self.history = []
        self.record = self.root / 'execution.json'
        if self.record.exists():
            old = json.loads(self.record.read_text())
            self.history.extend(old.get('history', []))
            for entry in old.get('sessions', [old] if 'session_id' in old else []):
                if entry.get('state') != 'closed':
                    entry['state'] = 'interrupted'
                    self.history.append(entry)
            self.save()
        self.alias_lock = asyncio.Lock()
        self.aliases = self.root / 'aliases'
        if self.aliases.is_symlink():
            raise OperationError('invalid alias directory')
        self.aliases.mkdir(mode=0o700, exist_ok=True)
        self.aliases.chmod(0o700)

    def profile(self, name):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name):
            raise OperationError('invalid profile name')
        path = self.root / 'profiles' / name
        if (self.root / 'profiles').is_symlink() or path.is_symlink():
            raise OperationError('invalid profile path')
        return path

    def session(self, sid):
        if sid not in self.sessions:
            raise OperationError('unknown or closed session')
        return self.sessions[sid].session(sid)

    def resource(self, sid):
        self.session(sid)
        return self.sessions[sid]

    @property
    def active(self):
        return next((s.active for s in self.sessions.values() if s.kind == 'main'), None)

    def info(self):
        return self.resource(self.active['session_id']).info() if self.active else None

    def save(self):
        atomic(self.record, {'sessions':[s.active for s in self.sessions.values() if s.active], 'history':self.history[-20:]})

    async def create(self, profile='legacy-main', alias=None, temporary=False):
        async with self.lock:
            if (self.root / 'maintenance.lock').exists():
                raise OperationError('maintenance in progress')
            if not temporary:
                if profile != 'legacy-main' or alias is not None:
                    raise OperationError('main requires legacy-main; use temporary with optional alias')
                if self.active:
                    return self.info()
                path = self.profile(profile)
                if not (path / 'native').is_dir() or (path / 'native').is_symlink():
                    raise OperationError('main native profile missing')
                state = None
            else:
                path = None
                state = self.load_alias(alias) if alias is not None else None
            if len(self.sessions) >= 2 or (temporary and any(s.kind=='temporary' for s in self.sessions.values())):
                raise OperationError('busy: maximum main plus one temporary session')
            s = Session(self, 'temporary' if temporary else 'main')
            # Lifecycle lock serializes launches only, never page operations.
            await s.launch(path, state)
            sid = secrets.token_hex(16)
            s.active = {'session_id':sid, 'kind':s.kind, 'profile':profile if path else None,
                        'alias':alias, 'state':'automation'}
            self.sessions[sid] = s
            self.save()
            if not temporary:
                atomic(self.root / 'READY.json', s.info())
            return s.info()

    async def close(self, sid):
        async with self.lock:
            return await self.resource(sid).close(sid)

    def alias_path(self, name):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name):
            raise OperationError('invalid alias name')
        p = self.aliases / (name + '.json')
        if self.aliases.is_symlink() or p.is_symlink():
            raise OperationError('invalid alias path')
        return p

    def load_alias(self, name):
        p = self.alias_path(name)
        if not p.is_file():
            raise OperationError('unknown alias')
        if p.stat().st_size > 10*1024*1024:
            raise OperationError('alias too large')
        try:
            state = json.loads(p.read_text())
            if not isinstance(state, dict) or not isinstance(state.get('cookies'), list) or not isinstance(state.get('origins'), list):
                raise ValueError()
            return state
        except (ValueError, OSError):
            raise OperationError('invalid alias snapshot') from None

    def list_aliases(self):
        result = []
        for p in sorted(self.aliases.glob('*.json')):
            self.alias_path(p.stem)
            result.append({'name':p.stem, 'modified_at':p.stat().st_mtime})
        return result

    async def save_alias(self, sid, name, overwrite=False):
        p = self.alias_path(name)
        async with self.resource(sid).automation(sid):
            # Global lifecycle lock also serializes explicit alias writes.
            async with self.alias_lock:
                if p.exists() and not overwrite:
                    raise OperationError('alias exists; explicit overwrite required')
                state = await self.resource(sid).context.storage_state(indexed_db=True)
                if len(json.dumps(state).encode()) > 10*1024*1024:
                    raise OperationError('alias too large')
                atomic(p, state)
                return {'name':name, 'saved':True}
