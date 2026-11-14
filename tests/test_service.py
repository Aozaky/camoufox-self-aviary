import time
import asyncio
import pathlib
import sys
import pytest
sys.path.insert(0, '/home/kasm-user/scripts')
from service import Owner, Session

@pytest.mark.asyncio
async def test_profile_validation_and_stale_ids(tmp_path):
    owner = Owner(tmp_path)
    for name in ['../escape', '/tmp/escape', '.', 'a/b', '']:
        with pytest.raises(ValueError):
            owner.profile(name)
    with pytest.raises(ValueError, match='session'):
        owner.session('stale')
    assert owner.profile('valid-profile').parent == tmp_path / 'profiles'

@pytest.mark.asyncio
async def test_real_owner_lifecycle_and_persistence(tmp_path):
    owner = Owner(tmp_path)
    first = await owner.create(temporary=True)
    sid, pid = first['session_id'], first['page_ids'][0]
    try:
        with pytest.raises(ValueError, match='busy'):
            await owner.create(temporary=True)
        await owner.resource(sid).action(sid, pid, 'navigate', url='http://127.0.0.1:8888/test_page.html')
        await owner.resource(sid).action(sid, pid, 'click', selector='#clickme')
        assert 'MARKER_CLICKED' in await owner.resource(sid).action(sid, pid, 'snapshot')
        with pytest.raises(ValueError, match='page'):
            await owner.resource(sid).action(sid, 'stale', 'snapshot')
        resource = owner.resource(sid)
        async with resource.automation(sid):
            page = resource.page(pid)
            await page.evaluate("localStorage.setItem('overnight','persisted')")
            await resource.context.add_cookies([{'name':'overnight','value':'persisted','url':'http://127.0.0.1:8888','expires':time.time()+86400}])
        await owner.save_alias(sid, 'behavior-test')
    finally:
        for session_id in list(owner.sessions):
            await owner.close(session_id)
    second = await owner.create(temporary=True, alias='behavior-test')
    try:
        resource = owner.resource(second['session_id'])
        async with resource.automation(second['session_id']):
            page = resource.page(second['page_ids'][0])
            await page.goto('http://127.0.0.1:8888/test_page.html')
            assert await page.evaluate("localStorage.getItem('overnight')") == 'persisted'
            assert any(c['name']=='overnight' for c in await resource.context.cookies())
    finally:
        await owner.close(second['session_id'])

@pytest.mark.asyncio
async def test_handoff_drains_locks_and_never_auto_returns(tmp_path):
    manager = Owner(tmp_path)
    owner = Session(manager, 'main')
    owner.active = {'session_id':'s', 'profile':'p', 'state':'automation'}
    owner.pages = {}
    entered = asyncio.Event()
    finish = asyncio.Event()
    async def work():
        async with owner.automation('s'):
            entered.set()
            await finish.wait()
    task = asyncio.create_task(work())
    await entered.wait()
    handoff = asyncio.create_task(owner.handoff('s', 60))
    await asyncio.sleep(.01)
    assert not handoff.done()
    finish.set()
    await task
    ticket = await handoff
    assert owner.active['state'] == 'pending'
    with pytest.raises(ValueError, match='paused'):
        async with owner.automation('s'):
            pass
    claim = owner.claim(ticket['ticket'])
    assert owner.active['state'] == 'human'
    with pytest.raises(ValueError):
        owner.claim(ticket['ticket'])
    assert owner.authorized(claim)
    owner.expiry = time.time()-1
    assert not owner.authorized(claim)
    assert owner.active['state'] == 'human'
    # Expiry locks automation until a fresh scoped ticket is claimed and returned.
    fresh = await owner.handoff('s', 60)
    claim = owner.claim(fresh['ticket'])
    await owner.handback(claim)
    assert not owner.authorized(claim)
    async with owner.automation('s'):
        assert owner.active['state'] == 'returned'
