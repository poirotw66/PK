"""Bundle the existing reader assets and the Sites Worker into one ESM entrypoint."""
import base64
import json
import mimetypes
from pathlib import Path

root = Path(__file__).resolve().parents[1]
dist = root / 'dist'
assets = {}
for path in sorted(dist.rglob('*')):
    if not path.is_file() or path.relative_to(dist).parts[0] in {'server', '.openai'}:
        continue
    kind = {'.mjs':'text/javascript; charset=utf-8','.json':'application/json; charset=utf-8','.css':'text/css; charset=utf-8','.html':'text/html; charset=utf-8'}.get(path.suffix, mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
    assets['/'+path.relative_to(dist).as_posix()]={'body':base64.b64encode(path.read_bytes()).decode(),'type':kind}
head=json.loads((dist/'public/version.json').read_text())
head['snapshot_url']='/public/'+head['snapshot_url']
from squash_scheduler.public_feed import PublicSnapshot
schema=PublicSnapshot.model_json_schema()
code='const ASSETS='+json.dumps(assets,separators=(',',':'))+';\nconst BOOTSTRAP='+json.dumps(head)+';\nconst SCHEMA='+json.dumps(schema)+';\n'
prefix = f"workspaces/{head['workspace_id']}/"
code += 'const STORAGE_PREFIX=' + json.dumps(prefix) + ';\n'
code+=(dist/'ranking-rules.mjs').read_text().replace('export const ', 'const ')+'\n'+(root/'standings.mjs').read_text().replace('export function ', 'function ')+'\n'
code+=(root/'live-scores.mjs').read_text().replace('export function ', 'function ')+'\n'+(root/'worker/live-scores.mjs').read_text()+'\n'+(root/'worker/cloud-publish.mjs').read_text()+'\n'+(root/'worker/api.mjs').read_text()
(dist/'server').mkdir(exist_ok=True)
(dist/'server/index.js').write_text(code)
(dist/'.openai').mkdir(exist_ok=True)
local_hosting=root/'.openai/hosting.json'
(dist/'.openai/hosting.json').write_text(local_hosting.read_text() if local_hosting.is_file() else json.dumps({'r2':'BUCKET'}))
print('Built Worker with',len(assets),'reader assets')
