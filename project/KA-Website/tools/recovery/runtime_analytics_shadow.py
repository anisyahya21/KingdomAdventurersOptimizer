"""Disposable emulator analytics containment. No APK or original database writes.

Use setup while the game is stopped; cleanup force-stops only this test game.
The original bind view MUST be private before covering the app path. Hash the
original view during an oracle run, and hash the normal path after cleanup.
"""
import argparse
import json
import shlex
import subprocess
import time

PKG = 'net.kairosoft.android.kingdom_en'
BASE = '/data/local/tmp/ka-analytics-shadow'
DB = '/data/user/0/' + PKG + '/databases'
CONTEXT = 'u:object_r:app_data_file:s0:c192,c256,c512,c768'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['setup', 'status', 'cleanup'])
    p.add_argument('--serial', default='emulator-5554')
    args = p.parse_args()
    adb = ['adb', '-s', args.serial]

    def root(*argv, check=True):
        return subprocess.run(adb + ['shell', shlex.join(['su', '0', *argv])],
                              capture_output=True, text=True, check=check).stdout.strip()

    def identity(path):
        return root('stat', '-c', '%d:%i', path)

    def db_hashes(path):
        return root('find', path, '-type', 'f', '-exec', 'sha256sum', '{}', ';').replace(path, DB)

    if args.action == 'setup':
        if root('pidof', PKG, check=False):
            raise RuntimeError('Stop the disposable game before mounting analytics shadow')
        if root('ls', '-d', BASE, check=False):
            raise RuntimeError('Shadow path already exists; inspect status/cleanup rather than overwrite')
        if root('stat', '-c', '%u', DB) != '10192':
            raise RuntimeError('Unexpected app UID; this fixture is pinned to the disposable emulator')
        names = root('ls', '-1', DB).splitlines()
        allowed = ('google_app_measurement_local.db', 'com.google.android.datatransport.events')
        if any(not any(n == a or n in (a+'-journal', a+'-wal', a+'-shm') for a in allowed) for n in names):
            raise RuntimeError('Unexpected database: scope must be reviewed before shadowing')
        root('mkdir', BASE)
        root('mount', '-t', 'tmpfs', '-o', 'size=32m,mode=0700', 'tmpfs', BASE)
        root('mkdir', BASE+'/original', BASE+'/volatile')
        root('mount', '--bind', DB, BASE+'/original')
        # Toybox syntax; --make-private alone incorrectly tries /etc/fstab.
        root('mount', '-o', 'private', 'none', BASE+'/original')
        root('cp', '-a', DB+'/.', BASE+'/volatile/')
        root('chown', '10192:10192', BASE+'/volatile')
        root('chmod', '770', BASE+'/volatile')
        root('chcon', '-R', CONTEXT, BASE+'/volatile')
        root('mount', '--bind', BASE+'/volatile', DB)

    mountinfo = root('cat', '/proc/1/mountinfo')
    if not any(' '+BASE+' ' in line and ' - tmpfs tmpfs ' in line for line in mountinfo.splitlines()):
        raise RuntimeError('Expected RAM-backed shadow root absent; no cleanup attempted')
    if not any(' '+DB+' ' in line and ' /volatile ' in line and ' - tmpfs tmpfs ' in line for line in mountinfo.splitlines()):
        raise RuntimeError('App database mount is not the expected volatile view')
    original_id, volatile_id, app_id = [identity(x) for x in (BASE+'/original', BASE+'/volatile', DB)]
    if original_id == volatile_id or app_id != volatile_id:
        raise RuntimeError('Original database view must differ from volatile/app view')
    report = dict(originalIdentity=original_id, volatileIdentity=volatile_id,
                  originalHashes=db_hashes(BASE+'/original'), volatileHashes=db_hashes(DB))
    pid = root('pidof', PKG, check=False)
    if pid:
        report['processIdentity'] = identity('/proc/'+pid+'/root/data/data/'+PKG+'/databases')
        if report['processIdentity'] != volatile_id:
            raise RuntimeError('Game mount namespace does not see the volatile database view')
    if args.action == 'cleanup':
        root('am', 'force-stop', PKG)
        # A pending Android data-transport job was measured restarting the app
        # 80ms after force-stop. Keep the RAM view mounted through that race.
        deadline = time.monotonic() + 10
        quiet_since = time.monotonic()
        restarts = 0
        while time.monotonic() - quiet_since < 2:
            if time.monotonic() > deadline:
                raise RuntimeError('App keeps restarting; leaving mounts intact')
            if root('pidof', PKG, check=False):
                root('am', 'force-stop', PKG)
                restarts += 1
                quiet_since = time.monotonic()
            time.sleep(.2)
        report['cleanupRestartRetries'] = restarts
        # Record the protected originals again, before removing any mounts.
        if db_hashes(BASE+'/original') != report['originalHashes']:
            raise RuntimeError('Original databases changed during quiescence; mounts retained')
        root('umount', DB)
        if identity(DB) != original_id or db_hashes(DB) != report['originalHashes']:
            raise RuntimeError('Original path/hash did not restore; leaving backup view intact')
        root('umount', BASE+'/original')
        root('umount', BASE)
        root('rmdir', BASE)
        report['restoredOriginalHashes'] = db_hashes(DB)
        report['cleaned'] = True
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
