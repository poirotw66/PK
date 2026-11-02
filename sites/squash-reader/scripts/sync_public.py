"""Watch confirmed local revisions and synchronize only the public projection."""
import argparse
import hashlib
import hmac
import json
import logging
import subprocess
import time
from pathlib import Path

from squash_scheduler.public_feed import PublicFeed
from squash_scheduler.storage.repository import Repository
from keychain import ACCOUNT, get_secret

SITE = 'https://' + ACCOUNT
LOG = logging.getLogger('public_sync')


def publish(path, value, secret):
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    timestamp=str(int(time.time()))
    signature=hmac.new(secret.encode(),timestamp.encode()+b'\n'+raw,hashlib.sha256).hexdigest()
    headers={'Content-Type':'application/json','X-Sync-Time':timestamp,'X-Sync-Signature':signature,'User-Agent':'PK-Public-Sync/1.0'}
    # Authentication travels through curl's stdin config, never process arguments.
    config='url = '+json.dumps(SITE+path)+'\nrequest = "POST"\nmax-time = 25\nsilent\nshow-error\n'
    for key,item in headers.items(): config+='header = '+json.dumps(key+': '+item)+'\n'
    # curl config data accepts escaped JSON safely; payload is trusted public JSON.
    config+='data-binary = '+json.dumps(raw.decode(),ensure_ascii=False)+'\nwrite-out = "\\n%{http_code}"\n'
    result=subprocess.run(['/usr/bin/curl','--config','-'],input=config,text=True,capture_output=True,timeout=30)
    if result.returncode: raise RuntimeError('Sync transport failed')
    body,code=result.stdout.rsplit('\n',1)
    if int(code) != 200: raise RuntimeError(f'Sync endpoint returned HTTP {code}')
    response=json.loads(body)
    if response.get('version') != value['version']: raise RuntimeError('Sync acknowledgement mismatch')
    return response


def run(repo, once=False):
    feed=PublicFeed()
    secret=get_secret()
    previous=None
    last_heartbeat=0
    while True:
        try:
            payload=feed.read(repo)
            if payload['version'] != previous:
                publish('/sync/update',payload,secret)
                previous=payload['version']
                last_heartbeat=time.monotonic()
                LOG.info('Published revision %s (%s matches)',payload['revision_id'],len(payload['matches']))
            elif time.monotonic()-last_heartbeat >= 30:
                publish('/sync/heartbeat',{'workspace_id':payload['workspace_id'],'version':previous},secret)
                last_heartbeat=time.monotonic()
            if once: return
        except Exception as exc:
            LOG.error('Public synchronization failed: %s',type(exc).__name__+': '+str(exc))
            previous=None
            if once: raise SystemExit(1) from None
            time.sleep(8)
        time.sleep(2)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    run(Repository(args.workspace),args.once)
