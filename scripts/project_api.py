"""Internal operation helper; never print sensitive responses."""
import os
import json,urllib.request,urllib.parse,pathlib,http.cookiejar,re
ROOT=pathlib.Path('/var/lib/docker/volumes/camoufox-m1-profile/_data/service')
def _api(name,args):
    req=urllib.request.Request('http://127.0.0.1:19280/api/'+name,data=json.dumps(args).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+(ROOT/'api.token').read_text().strip()})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(req,timeout=15 if name in ('control_status','request_human_takeover') else 120) as r:return json.load(r)
PUBLIC = os.environ.get('PUBLIC_BASE_URL','http://127.0.0.1:19280')

class ReclaimError(RuntimeError):
    """Sanitized reclaim failure; caller must not resume automation."""

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def _public_post(opener, path, data=None):
    req = urllib.request.Request(PUBLIC + path,
        data=json.dumps(data or {}).encode(),
        headers={'Content-Type':'application/json', 'Origin':PUBLIC})
    with opener.open(req, timeout=15) as response:
        return json.load(response)

def call(name, args=None):
    args = {} if args is None else args
    if name != 'reclaim_control':
        return _api(name, args)
    # Explicit local action only. No caller-selected origins or implicit session.
    sid = args.get('session_id')
    if set(args) != {'session_id'} or not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', sid):
        raise ReclaimError('explicit valid session_id required')
    scoped = {'session_id':sid}
    try:
        state = _api('control_status', scoped)['state']
        if state in ('automation', 'returned'):
            return dict(scoped, state=state)
        if state not in ('human', 'pending', 'expired'):
            raise ValueError('unavailable')
        handoff = _api('request_human_takeover', dict(scoped, ttl_seconds=60))
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect(),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        query = '?session_id=' + urllib.parse.quote(sid, safe='')
        _public_post(opener, '/claim' + query, {'ticket':handoff['ticket']})
        _public_post(opener, '/return' + query)
        if _api('control_status', scoped)['state'] != 'returned':
            raise ValueError('unconfirmed')
        return dict(scoped, state='returned')
    except Exception:
        # Do not retry ambiguous writes or expose transport URLs/tickets/cookies.
        try:
            observed = _api('control_status', scoped)['state']
            if observed not in ('automation','returned','human','pending','expired'):
                observed = 'unavailable'
        except Exception:
            observed = 'unknown'
        raise ReclaimError('reclaim unconfirmed; automation must remain paused; state=' + observed) from None

if __name__=='__main__':
    listing=call('list_sessions')
    sessions=listing.get('sessions',[listing['active']] if listing['active'] else [])
    assert all(s['state'] in ('automation','returned') for s in sessions), 'protected session; no close performed'
    for session in sessions:call('close_session',{'session_id':session['session_id']})
    print('PASS project browsers gracefully closed')
