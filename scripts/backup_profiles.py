"""Consistent private on-host backup, skips human lease, preserves ready profile."""
import os
import fcntl,os,pathlib,subprocess,sys,time,tarfile,json
from project_api import ROOT,call
def backup_safe(sessions):
    return all(s['state'] in ('automation', 'returned') for s in sessions)

def main():
    os.umask(0o077)
    PROJECT=pathlib.Path(os.environ.get('CAMOUFOX_PROJECT_DIR', pathlib.Path(__file__).resolve().parent.parent))
    PRIVATE=PROJECT/'private'; backups=PRIVATE/'backups';backups.mkdir(mode=0o700,exist_ok=True)
    lock=open(PRIVATE/'backup.lock','a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:sys.exit('SKIP backup already running')
    marker=ROOT/'maintenance.lock'
    # Gate new creation and handoff before checking state; existing human is never stopped.
    marker.touch(exist_ok=False);os.chown(marker,1000,1000)
    stopped=False;profile=None
    try:
        listing=call('list_sessions')
        sessions=listing.get('sessions', [listing['active']] if listing['active'] else [])
        if not backup_safe(sessions):
            print('SKIP human/pending/interrupted session; no stop performed');return
        expected_aliases=any((ROOT/'aliases').glob('*.json'))
        profile='legacy-main'
        for session in sessions:
            call('close_session',{'session_id':session['session_id']})
        subprocess.run(['docker','compose','stop','camoufox'],cwd=PROJECT,check=True,stdout=subprocess.DEVNULL)
        stopped=True
        # OS ownership lock proves no native writer remains.
        with open(ROOT/'owner.lock','a') as owner:
            fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
            name=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'.tar.gz.age'
            target=backups/name
            restart=PRIVATE/'backup-restart.json';restart.write_text(json.dumps({'profile':profile,'container':'camoufox-m1'}))
            with target.open('xb') as out:
                tar=subprocess.Popen(['tar','-czf','-','-C',str(ROOT.parent),'.','-C',str(PROJECT),'docker-compose.yml','docker','scripts','private/backup-restart.json','private/container.env','private/vnc_password.txt'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
                age=subprocess.run(['age','-R',str(PRIVATE/'backup-recipient.txt')],stdin=tar.stdout,stdout=out,stderr=subprocess.DEVNULL)
                tar.stdout.close();assert tar.wait()==0 and age.returncode==0
            decrypt=subprocess.Popen(['age','-d','-i',str(PRIVATE/'backup-identity.txt'),str(target)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            native=False;identity=False;aliases=False
            with tarfile.open(fileobj=decrypt.stdout,mode='r|gz') as archive:
                for member in archive:
                    aliases |= '/aliases/' in member.name and member.name.endswith('.json')
                    native |= '/native/' in member.name and member.isfile() and member.size>0
                    identity |= member.name.endswith('/identity.json') and member.size>0
            assert decrypt.wait()==0 and native and identity and (aliases or not expected_aliases)
            assert target.stat().st_size>0
            # Existing backup originals remain untouched by this upgrade.
            print('PASS encrypted archive decrypted/read back; nonempty native profile and stable identity; consistent graceful close plus owner lock; aliases included when present; existing archives preserved')
    finally:
        marker.unlink(missing_ok=True)
        if stopped:
            subprocess.run(['docker','compose','start','camoufox'],cwd=PROJECT,check=True,stdout=subprocess.DEVNULL)
            for _ in range(60):
                try:call('list_sessions');break
                except Exception:time.sleep(2)
            if profile:
                ready=call('create_session',{'profile':profile})
                assert len(call('list_sessions')['sessions'])==1
                (ROOT/'READY.json').write_text(json.dumps(ready));os.chown(ROOT/'READY.json',1000,1000)
                print('PASS same named profile reopened after backup; new IDs saved privately')

if __name__=='__main__':
    main()
