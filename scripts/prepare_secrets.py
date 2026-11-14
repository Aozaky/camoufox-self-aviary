"""Idempotent private project setup. Never prints credential values."""
from pathlib import Path
import os
import secrets

root = Path(__file__).resolve().parent.parent/'private'
root.mkdir(mode=0o700, exist_ok=True)
root.chmod(0o700)
password_path = root/'vnc_password.txt'
if not password_path.exists():
    fd = os.open(password_path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(fd,'w') as f:
        f.write(secrets.token_urlsafe(32))
password_path.chmod(0o600)
password = password_path.read_text().strip()
if not password or not all(c.isalnum() or c in '_-' for c in password):
    raise SystemExit('unsupported credential format; preserve private file and review locally')
env = root/'container.env'
lines = env.read_text().splitlines() if env.exists() else []
lines = [line for line in lines if not line.startswith(('VNC_PW=','CAMOUFOX_VIEWER_PASSWORD='))]
lines += ['VNC_PW='+password, 'CAMOUFOX_VIEWER_PASSWORD='+password]
if not any(line.startswith('VNC_RESOLUTION=') for line in lines):
    lines.append('VNC_RESOLUTION=1024x768')
fd = os.open(env, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
with os.fdopen(fd,'w') as f:
    f.write('\n'.join(lines)+'\n')
env.chmod(0o600)
print('Private project credentials ready; values were not printed.')
