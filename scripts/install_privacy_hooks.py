"""Install local-only privacy hooks; never copy the protected roster into Git."""
import argparse
from pathlib import Path
import subprocess
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--roster',type=Path,required=True)
args=parser.parse_args()
if not args.roster.is_file():raise SystemExit('Private roster must be an existing local snapshot')
subprocess.run(['git','config','--local','privacy.roster',str(args.roster.resolve())],cwd=root,check=True)
for name,mode in [('pre-commit','--staged'),('pre-push','--history')]:
    hook=root/'.git/hooks'/name
    if hook.exists():raise SystemExit(f'Existing {name} hook requires manual integration')
    hook.write_text('#!/bin/sh\nset -eu\nrepo_root=$(git rev-parse --show-toplevel)\nexec python3 "$repo_root/scripts/check_privacy.py" '+mode+' --require-roster\n')
    hook.chmod(0o755)
print('Installed privacy hooks. The roster path is stored only in local Git configuration.')
