// Verify the built application and its pure contracts using isolated fixtures.
import { build } from 'esbuild';
import { readdir, access } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
process.chdir(root);
if(Number(process.versions.node.split('.')[0])<22)throw new Error('Use Node 22 or later.');
await access('dist/server/wrangler.json');
const entries=['lib/encouragement-model.ts','lib/dispatch-model.ts','lib/coach-rules-model.ts','lib/local-return-model.ts','lib/local-update-model.ts','lib/publication-model.ts','lib/scoring.ts','lib/result-review-model.ts','lib/backup-model.ts','lib/advancement-model.ts','lib/court-queue.ts','lib/publication-diff.ts','lib/sync-service-auth.ts',...(await readdir('lib/schedule')).filter(file=>file.endsWith('.ts')).map(file=>'lib/schedule/'+file)];
await build({entryPoints:entries,bundle:true,platform:'node',format:'esm',outdir:'outputs',outExtension:{'.js':'.mjs'},entryNames:'[name]'});
const external=new Set(['http.test.mjs','live-http.test.mjs','court-queue-http.test.mjs','sync-service-http.test.mjs']);
const files=(await readdir('tests')).filter(file=>file.endsWith('.test.mjs')&&!external.has(file)).sort().map(file=>'tests/'+file);
const child=spawn(process.execPath,['--test',...files],{cwd:root,env:process.env,stdio:'inherit'});
const code=await new Promise((resolve,reject)=>{child.on('error',reject);child.on('exit',code=>resolve(code??1));});
process.exitCode=Number(code);
