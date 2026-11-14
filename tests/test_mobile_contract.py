"""Small regression for mobile claim gating; no running browser access."""
from pathlib import Path

def test_claim_fragment_is_retained_until_success():
    source=Path('scripts/gateway.py').read_text()
    human=source.split("HUMAN = '''",1)[1].split("'''",1)[0]
    assert "history.replaceState(null,'','/human'+scoped)" not in human.split('async function refresh')[0], 'ticket removed before claim succeeds'
