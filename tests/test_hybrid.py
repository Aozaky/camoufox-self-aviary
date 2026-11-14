"""Focused hybrid invariants, no credential fixtures."""
import asyncio
import sys
import pytest
sys.path.insert(0, '/home/kasm-user/scripts')
from service import Owner, Session, OperationError

@pytest.mark.asyncio
async def test_session_lock_page_and_claim_isolation(tmp_path):
    owner = Owner(tmp_path)
    a, b = Session(owner, 'main'), Session(owner, 'temporary')
    for s in (a,b):
        s.active = {'session_id':s.kind, 'profile':None, 'kind':s.kind, 'state':'automation'}
        s.pages = {}
        owner.sessions[s.kind] = s
    a.pages['a-page'] = object()
    with pytest.raises(OperationError):
        b.page('a-page')
    h = await a.handoff('main')
    with pytest.raises(OperationError):
        async with a.automation('main'): pass
    async with b.automation('temporary'): pass
    cid = a.claim(h['ticket'])
    assert a.authorized(cid) and not b.authorized(cid)
    async with b.automation('temporary'): pass
    await a.handback(cid)
    assert a.active['state']=='returned' and b.active['state']=='automation'
    expired = await a.handoff('main')
    a.expiry = 0
    with pytest.raises(OperationError): a.claim(expired['ticket'])
    with pytest.raises(OperationError): a.claim(None)
    with pytest.raises(OperationError): a.claim('x'*129)
    assert a.active['state']=='pending'

@pytest.mark.asyncio
async def test_alias_validation_and_overwrite(tmp_path):
    owner = Owner(tmp_path)
    for name in ['../bad','a/b','','.','x.json']:
        with pytest.raises(OperationError): owner.alias_path(name)
    p = owner.alias_path('fixture')
    p.symlink_to(tmp_path/'elsewhere')
    with pytest.raises(OperationError): owner.alias_path('fixture')
    p.unlink()
    with pytest.raises(OperationError): owner.load_alias('unknown')
    with p.open('wb') as f: f.truncate(10*1024*1024+1)
    with pytest.raises(OperationError): owner.load_alias('fixture')
    p.unlink()
    class Context:
        async def storage_state(self, **kwargs):
            assert kwargs=={'indexed_db':True}
            return {'cookies':[], 'origins':[]}
    s=Session(owner,'temporary');s.active={'session_id':'s','state':'automation'};s.context=Context()
    owner.sessions['s']=s
    await owner.save_alias('s','fixture')
    assert p.stat().st_mode & 0o777 == 0o600
    with pytest.raises(OperationError): await owner.save_alias('s','fixture')
    await owner.save_alias('s','fixture',True)
    assert owner.list_aliases()[0]['name']=='fixture'

def test_scoped_http_claims(tmp_path):
    from gateway import build_app
    from starlette.testclient import TestClient
    owner=Owner(tmp_path)
    for sid,kind in [('a','main'),('b','temporary')]:
        s=Session(owner,kind);s.active={'session_id':sid,'kind':kind,'state':'automation'}
        owner.sessions[sid]=s
    app=build_app(owner,'test-bearer','test-key','http://testserver',ensure_main=False)
    with TestClient(app) as c:
        assert c.post('/api/list_sessions',json={}).status_code==401
        def ticket(sid):
            r=c.post('/api/request_human_takeover',json={'session_id':sid},headers={'Authorization':'Bearer test-bearer'})
            assert r.status_code==200
            return r.json()['ticket']
        a,b=ticket('a'),ticket('b')
        origin={'Origin':'http://testserver'}
        assert c.post('/claim?session_id=b',json={'ticket':a},headers=origin).status_code==403
        assert c.post('/claim?session_id=a',json={'ticket':a},headers=origin).status_code==200
        assert c.post('/claim?session_id=b',json={'ticket':b},headers=origin).status_code==200
        assert c.get('/human?session_id=a').status_code==200
        assert c.get('/human/status?session_id=a').status_code==200
        assert c.get('/human/a/status').status_code==200
        assert c.get('/human/b/status').status_code==200
        assert c.post('/return?session_id=a',headers=origin).status_code==200
        assert c.get('/human/a/status').status_code==403
        assert c.get('/human/b/status').status_code==200
        assert c.post('/return?session_id=b',headers=origin).status_code==200

def test_backup_any_human_is_busy():
    from backup_profiles import backup_safe
    assert backup_safe([{'state':'automation'},{'state':'returned'}])
    assert not backup_safe([{'state':'automation'},{'state':'pending'}])
    assert not backup_safe([{'state':'human'},{'state':'automation'}])

def test_info_reports_only_live_pages(tmp_path):
    s=Session(Owner(tmp_path),'main');s.active={'session_id':'s','state':'automation'}
    class Page:
        def __init__(self,closed):self.closed=closed
        def is_closed(self):return self.closed
    s.pages={'live':Page(False),'closed':Page(True)}
    assert s.info()['page_ids']==['live']
