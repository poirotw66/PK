"""Install the current user's macOS launch agent for confirmed public-data sync."""
import os
import plistlib
import subprocess
from pathlib import Path

root=Path(__file__).resolve().parents[1]
scheduler=root.parents[1]/'squash-scheduler'
label='com.pk.squash-public-sync'
agent=Path.home()/'Library/LaunchAgents'/f'{label}.plist'
logs=Path.home()/'Library/Logs/PKPublicSync'
logs.mkdir(parents=True,exist_ok=True)
agent.parent.mkdir(parents=True,exist_ok=True)
config={'Label':label,'ProgramArguments':[str(scheduler/'.venv/bin/python'),'-u',str(root/'scripts/sync_public.py'),'--workspace',str(scheduler/'output/operator-demo-final/full-roster')],'WorkingDirectory':str(root),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':30,'StandardOutPath':str(logs/'sync.log'),'StandardErrorPath':str(logs/'sync.log')}
agent.write_bytes(plistlib.dumps(config))
agent.chmod(0o600)
domain=f'gui/{os.getuid()}'
existing=subprocess.run(['launchctl','print',domain+'/'+label],capture_output=True)
if existing.returncode==0: subprocess.run(['launchctl','bootout',domain+'/'+label],check=True)
subprocess.run(['launchctl','bootstrap',domain,str(agent)],check=True)
print('Installed login background sync:',label)
print('Logs:',logs/'sync.log')
