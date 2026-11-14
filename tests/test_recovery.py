import json
import sys
import pytest
sys.path.insert(0, '/home/kasm-user/scripts')
from service import Owner, atomic


def test_exclusive_owner_and_interrupted_record(tmp_path):
    atomic(tmp_path/'execution.json', {'session_id':'old','profile':'p','state':'human'})
    owner = Owner(tmp_path)
    assert owner.active is None
    assert owner.history[0]['state'] == 'interrupted'
    assert json.loads((tmp_path/'execution.json').read_text())['history'][0]['state'] == 'interrupted'
    with pytest.raises(RuntimeError, match='another browser owner'):
        Owner(tmp_path)
    owner.process_lock.close()
    reopened = Owner(tmp_path)
    assert reopened.active is None
    assert reopened.history[0]['state']=='interrupted'
    reopened.process_lock.close()
