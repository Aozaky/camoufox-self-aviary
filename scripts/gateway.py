"""Authenticated MCP and scoped KasmVNC HTTP/WebSocket gateway."""
import asyncio
import base64
import os
import pathlib
import secrets
import ssl
import functools
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import aiohttp
from itsdangerous import URLSafeSerializer, BadSignature
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse, Response
from starlette.routing import Route, WebSocketRoute, Mount
from service import Owner, OperationError


def build_app(owner, bearer, signing, base_url, ensure_main=True):
    base_url = base_url.rstrip('/')
    signer = URLSafeSerializer(signing, salt='human-control')
    secure = base_url.startswith('https:')
    mcp = FastMCP('Camoufox', stateless_http=True, json_response=True,
        instructions='Use explicit session_id and page_id. Handoff pauses only the selected session; only the operator can return control. Never ask for credentials in chat.',
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=[urlsplit(base_url).netloc, '127.0.0.1:19280', 'localhost:19280', 'testserver'],
            allowed_origins=[base_url, 'http://127.0.0.1:19280']))
    tools = {}
    def tool(fn):
        @functools.wraps(fn)
        async def guarded(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except OperationError:
                raise
            except (ValueError, TypeError, KeyError):
                raise ValueError('invalid operation, IDs, arguments, or automation paused') from None
            except Exception:
                raise ValueError('browser operation failed') from None
        tools[fn.__name__] = guarded
        return mcp.tool()(guarded)

    @tool
    async def list_sessions() -> dict:
        # Drain any in-flight launch before the maintenance gate snapshots resources.
        async with owner.lock:
            return {'active':owner.info(), 'sessions':[s.info() for s in owner.sessions.values()], 'history':owner.history}

    @tool
    async def create_session(profile: str = 'legacy-main', alias: str | None = None, temporary: bool = False) -> dict:
        """Reuse main, or explicitly create one isolated temporary session with an optional saved alias."""
        return await owner.create(profile, alias, temporary)

    @tool
    async def list_login_states() -> dict:
        """Metadata only; snapshots are explicit independent credential copies."""
        return {'aliases':owner.list_aliases()}

    @tool
    async def save_login_state(session_id: str, alias: str, overwrite: bool = False) -> dict:
        """Explicitly save selected session state; no raw credential data is returned."""
        return await owner.save_alias(session_id, alias, overwrite)

    @tool
    async def close_session(session_id: str) -> dict:
        return await owner.close(session_id)

    @tool
    async def list_tabs(session_id: str) -> dict:
        async with owner.resource(session_id).automation(session_id):
            # Include popups without ever reusing stale IDs.
            for p in owner.resource(session_id).context.pages:
                if p not in owner.resource(session_id).pages.values():
                    owner.resource(session_id).pages[secrets.token_hex(12)] = p
            return {'tabs':[{'page_id':pid, 'url':p.url} for pid,p in owner.resource(session_id).pages.items() if not p.is_closed()]}

    @tool
    async def create_tab(session_id: str) -> dict:
        async with owner.resource(session_id).automation(session_id):
            pid = secrets.token_hex(12)
            owner.resource(session_id).pages[pid] = await owner.resource(session_id).context.new_page()
            return {'page_id':pid}

    @tool
    async def close_tab(session_id: str, page_id: str) -> dict:
        async with owner.resource(session_id).automation(session_id):
            await owner.resource(session_id).page(page_id).close()
            owner.resource(session_id).pages.pop(page_id, None)
            return {'closed':page_id}

    @tool
    async def navigate(session_id: str, page_id: str, url: str) -> dict:
        return await owner.resource(session_id).action(session_id, page_id, 'navigate', url=url)

    @tool
    async def snapshot(session_id: str, page_id: str) -> str:
        return await owner.resource(session_id).action(session_id, page_id, 'snapshot')

    @tool
    async def click(session_id: str, page_id: str, selector: str) -> dict:
        return await owner.resource(session_id).action(session_id, page_id, 'click', selector=selector)

    @tool
    async def type_text(session_id: str, page_id: str, selector: str, text: str) -> dict:
        return await owner.resource(session_id).action(session_id, page_id, 'type', selector=selector, text=text)

    @tool
    async def scroll(session_id: str, page_id: str, pixels: int = 600) -> dict:
        return await owner.resource(session_id).action(session_id, page_id, 'scroll', pixels=pixels)

    @tool
    async def screenshot(session_id: str, page_id: str) -> dict:
        async with owner.resource(session_id).automation(session_id):
            data = await owner.resource(session_id).page(page_id).screenshot(type='png')
            return {'mime_type':'image/png', 'base64':base64.b64encode(data).decode()}

    def transfer_path(session_id, file_id):
        import re
        if not re.fullmatch(r'[a-f0-9]{32}', file_id):
            raise ValueError('invalid file ID')
        folder = owner.root / 'transfers' / session_id
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        return folder / file_id

    @tool
    async def upload_file(session_id: str, page_id: str, selector: str, base64_data: str) -> dict:
        """Upload at most 5 MiB of supplied bytes; never reads a host path."""
        async with owner.resource(session_id).automation(session_id):
            page = owner.resource(session_id).page(page_id)
            if len(base64_data) > 7_000_000:
                raise ValueError('file too large')
            data = base64.b64decode(base64_data, validate=True)
            if len(data) > 5*1024*1024:
                raise ValueError('file too large')
            file_id = secrets.token_hex(16)
            path = transfer_path(session_id, file_id)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            await page.locator(selector).set_input_files(str(path))
            return {'file_id':file_id, 'bytes':len(data)}

    @tool
    async def download_file(session_id: str, page_id: str, selector: str) -> dict:
        """Click a download and retain it under private state using an opaque ID.

        Returns metadata only. Downloaded content is never executed or opened.
        """
        async with owner.resource(session_id).automation(session_id):
            page = owner.resource(session_id).page(page_id)
            async with page.expect_download() as event:
                await page.locator(selector).click()
            download = await event.value
            file_id = secrets.token_hex(16)
            path = transfer_path(session_id, file_id)
            await download.save_as(str(path))
            path.chmod(0o600)
            size = path.stat().st_size
            if size > 20*1024*1024:
                path.unlink()
                raise ValueError('download exceeds 20 MiB retained-file limit')
            return {'file_id':file_id, 'bytes':size}

    @tool
    async def request_human_takeover(session_id: str, ttl_seconds: int = 300) -> dict:
        result = await owner.resource(session_id).handoff(session_id, ttl_seconds)
        result['url'] = base_url + '/human?session_id=' + session_id + '#' + result['ticket']
        return result

    @tool
    async def control_status(session_id: str) -> dict:
        return dict(owner.session(session_id))

    @tool
    async def await_takeover_return(session_id: str, timeout_seconds: int = 30) -> dict:
        for _ in range(max(1, min(60, timeout_seconds))*5):
            state = owner.session(session_id)['state']
            if state in ('returned', 'closed', 'interrupted'):
                break
            await asyncio.sleep(.2)
        return dict(owner.session(session_id))

    def target(request):
        sid = request.path_params.get('sid') or request.query_params.get('session_id')
        try:
            return owner.resource(sid)
        except OperationError:
            return None

    def claim_of(request):
        session = target(request)
        if not session:
            return None
        sid = session.active['session_id']
        try:
            value = signer.loads(request.cookies.get('camo_claim_' + sid, ''))
            return value['claim'] if value.get('sid') == sid else None
        except (BadSignature, ValueError, TypeError, KeyError):
            return None

    def origin_ok(request):
        return request.headers.get('origin') == base_url

    async def api(request):
        try:
            name = request.path_params['tool']
            if name not in tools:
                return JSONResponse({'error':'unknown tool'}, 404)
            result = await tools[name](**await request.json())
            return JSONResponse(result)
        except OperationError as exc:
            return JSONResponse({'error':str(exc)}, 409)
        except (ValueError, TypeError, KeyError):
            return JSONResponse({'error':'invalid operation, IDs, arguments, or automation paused'}, 409)
        except Exception:
            # Playwright errors can include page content and input values.
            return JSONResponse({'error':'browser operation failed'}, 500)

    async def health(request):
        return JSONResponse({'ok':True})

    async def human(request):
        return HTMLResponse(HUMAN) if target(request) else Response(status_code=404)

    async def claim(request):
        if not origin_ok(request):
            return JSONResponse({'error':'origin denied'}, 403)
        try:
            session = target(request)
            if not session:
                raise OperationError('unknown session')
            async with session.lock:
                cid = session.claim((await request.json())['ticket'])
            sid = session.active['session_id']
            response = JSONResponse({'state':'human'})
            response.set_cookie('camo_claim_' + sid, signer.dumps({'sid':sid, 'claim':cid}), httponly=True,
                secure=secure, samesite='strict', max_age=900, path='/')
            return response
        except (ValueError, KeyError, TypeError):
            return JSONResponse({'error':'invalid or expired ticket'}, 403)

    async def handback(request):
        if not origin_ok(request):
            return JSONResponse({'error':'origin denied'}, 403)
        try:
            session = target(request)
            if not session:
                raise OperationError('unknown session')
            result = await session.handback(claim_of(request))
            response = JSONResponse(result)
            response.delete_cookie('camo_claim_' + session.active['session_id'], path='/')
            return response
        except ValueError:
            return JSONResponse({'error':'claim denied'}, 403)

    async def human_status(request):
        session = target(request)
        if not session or not session.authorized(claim_of(request)):
            return JSONResponse({'error':'Viewer claim expired, revoked or returned. If control was not handed back, request a fresh link.'}, 403)
        return JSONResponse(dict(session.active, expires_at=session.expiry))

    # Keep Kasm credentials exclusively server-side. Ignore all incoming auth/query
    # cookies when proxying; users cannot choose another upstream or port.
    auth = aiohttp.BasicAuth('kasm_user', os.environ.get('CAMOUFOX_VIEWER_PASSWORD', ''))
    ssl_context = ssl.create_default_context()
    # Only the fixed loopback self-signed Kasm endpoint uses this context.
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    async def viewer(request):
        session = target(request)
        if not session or not session.authorized(claim_of(request)):
            return Response(status_code=403)
        path = request.path_params['path']
        if '..' in path.split('/') or '\\' in path:
            return Response(status_code=400)
        async with aiohttp.ClientSession(auth=auth) as client:
            async with client.get(f'https://127.0.0.1:{session.port}/' + path,
                    params=request.query_params, ssl=ssl_context, allow_redirects=False) as upstream:
                data = await upstream.read()
                if not session.authorized(claim_of(request)):
                    return Response(status_code=403)
                return Response(data, upstream.status,
                    headers={'Content-Type':upstream.headers.get('Content-Type','application/octet-stream'),
                             'Cache-Control':'no-store'})

    async def websocket(ws):
        session = target(ws)
        cid = claim_of(ws)
        if not session or not origin_ok(ws) or not session.authorized(cid):
            await ws.close(code=4403)
            return
        path = ws.path_params['path']
        if '..' in path.split('/') or '\\' in path:
            await ws.close(code=4403)
            return
        protocols = [p.strip() for p in ws.headers.get('sec-websocket-protocol','').split(',') if p.strip()]
        async with aiohttp.ClientSession(auth=auth) as client:
            try:
                async with client.ws_connect(f'wss://127.0.0.1:{session.port}/' + path,
                        params=ws.query_params, ssl=ssl_context, protocols=protocols,
                        origin=f'https://127.0.0.1:{session.port}', max_msg_size=16*1024*1024) as upstream:
                    if not session.authorized(cid):
                        await ws.close(code=4403)
                        return
                    await ws.accept(subprotocol=upstream.protocol)
                    session.connections.add(upstream)
                    async def to_kasm():
                        while session.authorized(cid):
                            event = await ws.receive()
                            if event['type'] == 'websocket.disconnect':
                                return
                            if not session.authorized(cid):
                                return
                            if event.get('bytes') is not None:
                                await upstream.send_bytes(event['bytes'])
                            elif event.get('text') is not None:
                                await upstream.send_str(event['text'])
                    async def to_browser():
                        async for msg in upstream:
                            if not session.authorized(cid):
                                return
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                await ws.send_bytes(msg.data)
                            elif msg.type == aiohttp.WSMsgType.TEXT:
                                await ws.send_text(msg.data)
                            else:
                                return
                    async def expiry():
                        while session.authorized(cid):
                            await asyncio.sleep(.1)
                    tasks = [asyncio.create_task(fn()) for fn in (to_kasm, to_browser, expiry)]
                    try:
                        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        # Input channel closes even if ticket expires with no traffic.
                        await upstream.close()
                        session.connections.discard(upstream)
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            except (aiohttp.ClientError, RuntimeError, ValueError, TimeoutError):
                pass
            finally:
                try:
                    await ws.close()
                except RuntimeError:
                    pass

    mcp_app = mcp.streamable_http_app()
    @asynccontextmanager
    async def lifespan(app):
        if ensure_main:
            await owner.create()
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            for session in list(owner.sessions.values()):
                await session.disconnect()
                if session.active['state'] in ('automation', 'returned'):
                    if hasattr(session, 'cm'):
                        await owner.close(session.active['session_id'])
                else:
                    session.active['state'] = 'interrupted'
                    owner.save()
                    if hasattr(session, 'cm'):
                        try: await session.cm.__aexit__(None, None, None)
                        except Exception: pass
                    await session.stop_display()

    app = Starlette(routes=[Route('/health', health), Route('/human', human), Route('/human/status', human_status), Route('/human/{sid}', human),
        Route('/claim', claim, methods=['POST']), Route('/return', handback, methods=['POST']),
        Route('/human/{sid}/status', human_status), Route('/api/{tool}', api, methods=['POST']),
        Route('/viewer/{sid}/{path:path}', viewer), WebSocketRoute('/viewer/{sid}/{path:path}', websocket),
        Mount('/', app=mcp_app)], lifespan=lifespan)

    class Security:
        def __init__(self, app):
            self.app = app
        async def __call__(self, scope, receive, send):
            if scope['type'] == 'http':
                path = scope['path']
                if path.startswith(('/api/', '/mcp')):
                    headers = dict(scope['headers'])
                    supplied = headers.get(b'authorization', b'')
                    if not secrets.compare_digest(supplied, ('Bearer ' + bearer).encode()):
                        await JSONResponse({'error':'unauthorized'}, 401)(scope, receive, send)
                        return
                async def secured(message):
                    if message['type'] == 'http.response.start':
                        message['headers'] += [(b'cache-control', b'no-store'),
                            (b'referrer-policy', b'no-referrer'), (b'x-content-type-options', b'nosniff'),
                            (b'x-frame-options', b'SAMEORIGIN')]
                    await send(message)
                await self.app(scope, receive, secured)
            else:
                await self.app(scope, receive, send)
    return Security(app)


HUMAN = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="referrer" content="no-referrer"><title>浏览器人工接管</title>
<style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;background:#15232d;color:#f4f6f8;font:15px system-ui}body{height:100vh;height:var(--viewport-height,100dvh);display:flex;flex-direction:column;padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left);overflow:hidden}header{padding:6px 10px}#status{margin:0;min-height:22px}nav{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:6px;padding:0 8px 6px}nav button{grid-column:span 2}nav button:nth-child(n+4){grid-column:span 3}button{font:inherit;font-weight:600;min-height:44px;border:1px solid #78909c;border-radius:7px;background:#243d4d;color:white;padding:7px 4px}button:disabled{opacity:.45}#keyboard{background:#155d75}#back{background:#704126}details{padding:0 10px 6px;font-size:13px}details p{margin:5px 0;max-height:22vh;overflow:auto}#viewer{flex:1;min-height:0;position:relative}iframe{display:block;border:0;width:100%;height:100%;background:#0c1821}#help{color:#d2e5ef}summary{min-height:25px;cursor:pointer}
</style></head><body>
<header><p id="status" role="status" aria-live="polite">正在确认接管权限…</p></header>
<main id="viewer" aria-label="远程浏览器"></main>
<nav aria-label="接管工具"><button id="keyboard" disabled>打开键盘</button><button id="fit" disabled>适应屏幕</button><button id="zoom" disabled>原尺寸放大</button><button id="reconnect" disabled>重新连接</button><button id="clipboard" disabled>原生剪贴板</button></nav>
<script>
'use strict';
const $=id=>document.getElementById(id), holder=$('viewer');
const candidate=new URLSearchParams(location.search).get('session_id')||location.pathname.split('/')[2];
const sid=/^[a-zA-Z0-9_-]{1,80}$/.test(candidate||'')?candidate:null;
const scoped='?session_id='+encodeURIComponent(sid||'');
let ticket=location.hash.slice(1), authorized=false, frame=null, epoch=0, busy=false, stopped=false;
function message(text){$('status').textContent=text;}
function clearViewer(){epoch++;holder.replaceChildren();frame=null;for(const id of ['keyboard','fit','zoom','clipboard'])$(id).disabled=true;}
function deny(text){authorized=false;clearViewer();$('reconnect').disabled=true;message(text);}
async function request(path,options={}){return fetch(path+scoped,{...options,credentials:'same-origin',cache:'no-store',redirect:'error'});}
async function refresh(){
 try{const r=await request('/human/status');const d=await r.json();
 if(!r.ok||d.session_id!==sid||d.state!=='human'){deny('权限无效或已过期，请在当前浏览器打开新的接管链接。');return false;}
 authorized=true;$('reconnect').disabled=false;return true;
 }catch{deny('权限检查失败，画面已关闭。请刷新重试；自动操作仍暂停。');return false;}
}
function nativeDocument(){
 if(!authorized||!frame)throw Error('no viewer');
 const d=frame.contentDocument;
 if(!d||!d.documentElement.classList.contains('noVNC_connected'))throw Error('not connected');
 return d;
}
function nativeAction(fn){try{fn(nativeDocument());}catch{message('画面尚未连接，请稍后重试或点「重新连接」。');}}
function resize(mode){nativeAction(d=>{const select=d.getElementById('noVNC_setting_resize');select.value=mode;select.dispatchEvent(new frame.contentWindow.Event('change',{bubbles:true}));message(mode==='scale'?'已适应屏幕。':'已放大至原尺寸，可使用原生触摸操作。');});}
// Keep this handler synchronous: iOS requires focus during the genuine user tap.
$('keyboard').onclick=()=>nativeAction(d=>{frame.contentWindow.focus();d.getElementById('noVNC_keyboard_button').click();message('先选择远程输入框，再使用手机键盘输入。');});
$('fit').onclick=()=>resize('scale');$('zoom').onclick=()=>resize('off');
async function openViewer(){
 if(busy||stopped)return;busy=true;clearViewer();
 try{if(!await refresh())return;
 const generation=epoch;
 // Check HTML authorization before navigation (legacy unauthorized vnc.html is a bare 403).
 const url='/viewer/'+encodeURIComponent(sid)+'/vnc.html?autoconnect=1&resize=scale&path=viewer/'+encodeURIComponent(sid)+'/websockify&logging=warn';
 const probe=await fetch(url,{credentials:'same-origin',cache:'no-store',redirect:'error'});
 if(!probe.ok){deny('画面权限失效，请请求新的接管链接。');return;}
 await probe.body?.cancel();if(generation!==epoch||!authorized)return;
 frame=document.createElement('iframe');frame.title='KasmVNC 远程浏览器';frame.src=url;
 frame.onload=()=>{if(!authorized)return;try{const d=frame.contentDocument;if(!d.getElementById('noVNC_keyboard_button')){deny('画面载入失败，请请求新的接管链接。');return;}for(const id of ['keyboard','fit','zoom','clipboard'])$(id).disabled=false;message('已载入画面；连接完成后可打开键盘。');}catch{deny('画面载入失败，请重试。');}};
 holder.replaceChildren(frame);
 }catch{deny('连接失败，画面已关闭。请刷新重试。');}finally{busy=false;}
}
$('reconnect').onclick=openViewer;
$('clipboard').onclick=()=>nativeAction(d=>{
 const panel=d.getElementById('noVNC_clipboard');
 if(!panel.dataset.mobile){
  d.body.appendChild(panel);panel.dataset.mobile='1';
  const style=d.createElement('style');style.textContent='#noVNC_clipboard{position:fixed!important;top:8px!important;left:8px!important;right:8px!important;max-width:calc(100% - 16px)!important;transform:none!important;z-index:10000}#noVNC_clipboard textarea{box-sizing:border-box;width:100%;max-width:100%}';d.head.appendChild(style);
 }
 d.getElementById('noVNC_clipboard_button').click();
 message('原生剪贴板已切换；修改文本后移开焦点以发送，再在远程网页粘贴。');
});
async function start(){
 if(!sid){deny('接管链接缺少有效会话，请请求新的链接。');return;}
 try{if(ticket){const r=await request('/claim',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticket})});
 if(!r.ok){deny('认领失败：链接过期或已在另一浏览器使用。请请求新链接，并首先在目标浏览器打开。');return;}
 ticket='';history.replaceState(null,'',location.pathname+location.search);
 }await openViewer();}catch{deny('认领连接失败，链接已保留，请刷新重试。');}
}
function viewport(){document.body.style.setProperty('--viewport-height',(window.visualViewport?.height||window.innerHeight)+'px');}
window.visualViewport?.addEventListener('resize',viewport);window.addEventListener('resize',viewport);viewport();
const timer=setInterval(()=>{if(authorized&&!busy&&!stopped)refresh();},5000);
start();
</script></body></html>
'''


def secret(path):
    path = pathlib.Path(path)
    if not path.exists():
        fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd,'w') as f:
            f.write(secrets.token_urlsafe(48))
    return path.read_text().strip()


def main():
    os.umask(0o077)
    import uvicorn
    root = pathlib.Path(os.environ.get('STATE_DIR','/home/kasm-user/camoufox-profile/service'))
    owner = Owner(root)
    bearer = secret(root / 'api.token')
    signing = secret(root / 'cookie.key')
    app = build_app(owner, bearer, signing, os.environ.get('PUBLIC_BASE_URL','http://127.0.0.1:19280'))
    uvicorn.run(app, host='0.0.0.0', port=19280, access_log=False, log_level='warning',
        proxy_headers=False, ws='websockets')

if __name__ == '__main__':
    main()
