"""Check staged files or every reachable Git blob without printing sensitive values."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANNED_DIRS = {'output','tournaments','dist','public-documents','evidence','private','.openai','.venv','.uv-cache','__pycache__'}
BANNED_SUFFIXES = {'.pdf','.doc','.docx','.xls','.xlsx','.csv','.tsv','.zip','.gz','.tar','.png','.jpg','.jpeg','.webp','.sqlite','.db','.pem','.key','.pyc'}
SECRET_PATTERNS = [re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{30,}\b'),re.compile(r'\bAKIA[0-9A-Z]{16}\b')]
FONT = 'squash-scheduler/src/squash_scheduler/assets/fonts/IBMPlexSans.woff2'

def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT)

def load_names(roster):
    if not roster:
        result=subprocess.run(['git','config','--get','privacy.roster'],cwd=ROOT,capture_output=True,text=True)
        roster=result.stdout.strip()
    if not roster:return []
    document=json.loads(Path(roster).expanduser().read_text(encoding='utf-8-sig'))
    snapshot=document.get('snapshot',document)
    names={''.join(unicodedata.normalize('NFKC', person[key]).split()).casefold() for person in snapshot.get('participants',[]) for key in ['official_name','display_name'] if isinstance(person.get(key),str) and len(''.join(person[key].split()))>=2}
    return sorted(names)

def check_content(path,raw,names):
    errors=[];p=Path(path)
    if any(part in BANNED_DIRS for part in p.parts) or p.suffix.lower() in BANNED_SUFFIXES or p.name.startswith('.env'):
        errors.append('prohibited private-data or generated-artifact path')
    if p.suffix.lower()=='.woff2':
        expected=json.loads((ROOT/'privacy-policy.json').read_text())['allowed_font_sha256']
        if path!=FONT or hashlib.sha256(raw).hexdigest()!=expected:errors.append('unapproved binary asset')
        return errors
    try:text=raw.decode('utf-8-sig')
    except UnicodeError:return errors+['unapproved binary content']
    normalized=unicodedata.normalize('NFKC', path+'\n'+text).casefold()
    flat=''.join(normalized.split())
    def found(name):
        if name.isascii() and name.isalpha():
            pattern=r'(?<![a-z])'+r'\s*'.join(map(re.escape,name))+r'(?![a-z])'
            return re.search(pattern,normalized) is not None
        return name in flat
    if any(found(name) for name in names):errors.append('protected roster name found')
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):errors.append('credential-like content found')
    if re.search(re.escape('/'+'Users'+'/') + r'[^/\s]+/',text):errors.append('personal absolute home path found')
    if re.search(r'appgprj_[a-f0-9]{20,}',text):errors.append('production Sites identity found')
    if re.search(r'[A-Za-z0-9+/]{1500,}={0,2}',text):errors.append('large embedded base64 payload found')
    return errors

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history',action='store_true')
    parser.add_argument('--staged',action='store_true')
    parser.add_argument('--roster')
    parser.add_argument('--require-roster',action='store_true')
    args=parser.parse_args()
    try:names=load_names(args.roster)
    except (OSError,ValueError):raise SystemExit('Privacy check failed: private roster cannot be loaded')
    if args.require_roster and not names:raise SystemExit('Privacy check failed: configure a local private roster first')
    records=[]
    if args.history:
        for row in git('rev-list','--objects','--all').decode().splitlines():
            parts=row.split(' ',1)
            kind=git('cat-file','-t',parts[0]).strip().decode()
            if kind=='blob' and len(parts)==2:records.append((parts[1],git('cat-file','blob',parts[0])))
            elif kind in ['commit','tag']:records.append((f'git-{kind}-{parts[0]}',git('cat-file',kind,parts[0])))
        records.append(('git-refs',git('for-each-ref','--format=%(refname)')))
    else:
        for item in git('ls-files','--stage','-z').split(b'\0'):
            if not item:continue
            metadata,path=item.split(b'\t',1);mode,sha,_=metadata.split()
            if mode not in [b'100644',b'100755']:raise SystemExit('Privacy check failed: symlinks and submodules are not allowed')
            records.append((path.decode(),git('cat-file','blob',sha.decode())))
    errors=[]
    for path,raw in records:
        errors.extend((path,error) for error in check_content(path,raw,names))
    for path,error in errors:print(f'{path}: {error}',file=sys.stderr)
    if errors:raise SystemExit(1)
    print(f'Privacy check passed: {len(records)} Git blobs; protected-name dictionary '+('enabled' if names else 'not configured'))

if __name__=='__main__':main()
