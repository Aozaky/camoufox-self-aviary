"""No credential values are included in assertions or response diagnostics."""
import sys
import pytest
sys.path.insert(0, '/home/kasm-user/scripts')
from starlette.testclient import TestClient
from gateway import build_app
from service import Owner, Session


def test_auth_claim_csrf_and_viewer_gate(tmp_path):
    owner = Owner(tmp_path)
    session = Session(owner, 'main')
    session.active = {'session_id':'s','profile':'legacy-main','state':'automation'}
    owner.sessions['s'] = session
    app = build_app(owner, 'test-bearer', 'test-signing-key', 'http://testserver', ensure_main=False)
    with TestClient(app) as c:
        assert c.get('/health').status_code == 200
        assert c.post('/api/list_sessions', json={}).status_code == 401
        assert c.get('/viewer/s/').status_code == 403
        assert c.get('/viewer/s/core/rfb.js').status_code == 403
        with pytest.raises(Exception):
            with c.websocket_connect('/viewer/s/websockify'):
                pass
        response = c.post('/api/request_human_takeover', json={'session_id':'s'}, headers={'Authorization':'Bearer test-bearer'})
        assert response.status_code == 200
        ticket = response.json()['ticket']
        assert c.post('/claim?session_id=s', json={'ticket':ticket}, headers={'Origin':'http://evil.invalid'}).status_code == 403
        assert c.post('/claim?session_id=s', json={'ticket':ticket}, headers={'Origin':'http://testserver'}).status_code == 200
        assert c.post('/claim?session_id=s', json={'ticket':ticket}, headers={'Origin':'http://testserver'}).status_code == 403
        assert c.post('/return?session_id=s', headers={'Origin':'http://evil.invalid'}).status_code == 403
        assert c.post('/return?session_id=s', headers={'Origin':'http://testserver'}).status_code == 200
        assert c.get('/viewer/s/').status_code == 403
