function stablePublicId(namespace,...parts) {
    const prefix=Uint8Array.from('6ba7b8119dad11d180b400c04fd430c8'.match(/../g),hex=>parseInt(hex,16));
    const name=new TextEncoder().encode('['+[namespace,...parts].map(value=>JSON.stringify(value)).join(', ')+']');
    const input=new Uint8Array(prefix.length+name.length);input.set(prefix);input.set(name,prefix.length);
    const padded=new Uint8Array(Math.ceil((input.length+9)/64)*64);padded.set(input);padded[input.length]=0x80;
    const data=new DataView(padded.buffer);data.setUint32(padded.length-4,input.length*8);
    const h=[0x67452301,0xefcdab89,0x98badcfe,0x10325476,0xc3d2e1f0],words=new Uint32Array(80);
    const rotate=(value,bits)=>(value<<bits)|(value>>>(32-bits));
    for(let offset=0;offset<padded.length;offset+=64){
        for(let i=0;i<16;i++)words[i]=data.getUint32(offset+4*i);
        for(let i=16;i<80;i++)words[i]=rotate(words[i-3]^words[i-8]^words[i-14]^words[i-16],1);
        let [a,b,c,d,e]=h;
        for(let i=0;i<80;i++){
            const f=i<20?(b&c)|(~b&d):i<40?b^c^d:i<60?(b&c)|(b&d)|(c&d):b^c^d;
            const k=i<20?0x5a827999:i<40?0x6ed9eba1:i<60?0x8f1bbcdc:0xca62c1d6;
            const next=(rotate(a,5)+f+e+k+words[i])>>>0;e=d;d=c;c=rotate(b,30);b=a;a=next;
        }
        [a,b,c,d,e].forEach((value,i)=>{h[i]=(h[i]+value)>>>0;});
    }
    const bytes=new Uint8Array(20),digest=new DataView(bytes.buffer);h.forEach((value,i)=>digest.setUint32(i*4,value));
    bytes[6]=(bytes[6]&15)|80;bytes[8]=(bytes[8]&63)|128;
    const hex=Array.from(bytes.slice(0,16),byte=>byte.toString(16).padStart(2,'0')).join('');
    return [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
}

const fields = ['source_id', 'sequence', 'schedule_revision', 'published_at', 'live_updated_at', 'created_at', 'matches'];
const matchFields = ['id', 'code', 'division', 'side_a', 'side_b', 'status', 'court_id', 'planned_start_at', 'started_at'];
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const iso = value => typeof value === 'string' && Number.isFinite(Date.parse(value)) && new Date(value).toISOString() === value;
function scoredResult(value, match) {
    const allowed = ['rules','winner','games', ...(Object.hasOwn(value ?? {}, 'outcome') ? ['outcome'] : [])];
    const outcome = value?.outcome ?? 'played';
    if (!exact(value, allowed) || !exact(value.rules, ['best_of', 'target', 'margin']) || ![3, 5].includes(value.rules.best_of) || ![11, 15].includes(value.rules.target) || value.rules.margin !== 2 || !['played','walkover','retired','double_forfeit'].includes(outcome) || !Array.isArray(value.games) || value.games.length > value.rules.best_of || [match.side_a, match.side_b].some(side => side.resolution !== 'confirmed' || !side.entry_id) || match.side_a.entry_id === match.side_b.entry_id)
        throw new Error('Invalid scoring result');
    if (outcome === 'double_forfeit' ? value.winner !== null : !['a','b'].includes(value.winner)) throw new Error('Invalid winner');
    if (['walkover','double_forfeit'].includes(outcome) && value.games.length) throw new Error('Unplayed outcome cannot contain scores');
    const wins = { a: 0, b: 0 }, needed = Math.floor(value.rules.best_of / 2) + 1;
    for (const [index, game] of value.games.entries()) {
        if (Math.max(wins.a,wins.b) >= needed || !exact(game,['side_a','side_b']) || ![game.side_a,game.side_b].every(score => Number.isInteger(score) && score >= 0 && score <= 999)) throw new Error('Invalid game score');
        const high = Math.max(game.side_a,game.side_b), terminal = Math.max(value.rules.target,Math.min(game.side_a,game.side_b)+2);
        if (high === terminal) wins[game.side_a > game.side_b ? 'a' : 'b']++;
        else if (outcome !== 'retired' || index !== value.games.length-1 || high >= terminal) throw new Error('Invalid game score');
    }
    if (outcome === 'played' && wins[value.winner] !== needed || outcome === 'retired' && Math.max(wins.a,wins.b) >= needed) throw new Error('Invalid match winner');
    return { outcome, winner_entry_id: value.winner === null ? null : (value.winner === 'a' ? match.side_a : match.side_b).entry_id, games: value.games };
}
function replacePublicSides(match, patch, registry, reviewed, restore) {
    for (const key of ['a','b']) {
        const side = match['side_'+key], name = patch['side_'+key], idKey = 'side_'+key+'_entry_id';
        if (!Object.hasOwn(patch,idKey)) { if (side.resolution === 'confirmed' && name !== side.name) throw new Error('Missing entry identity'); continue; }
        const id = patch[idKey];
        if (id === side.entry_id && name === side.name) continue;
        if (!reviewed || !restore && side.source_type === 'entry') throw new Error('Entry identity cannot change');
        if (id === null) {
            if (!restore && side.source_type === 'entry') throw new Error('Entry cannot become unresolved');
            Object.assign(side,{name,entry_id:null,resolution:'pending'}); continue;
        }
        if (typeof id !== 'string' || registry.get(match.division_id+'|'+id) !== name) throw new Error('Unknown entry identity');
        Object.assign(side,{name,entry_id:id,resolution:'confirmed'});
    }
}
function extendEncouragement(base, records, previous, patches, reviewed) {
    const fail = () => { throw new Error('Invalid encouragement draw'); };
    const ids = values => Array.isArray(values) && values.length <= 1000 && values.every(id => typeof id === 'string' && id.length > 0 && id.length <= 100) && new Set(values).size === values.length;
    const sameSet = (a,b) => a.length === b.length && a.every(id => b.includes(id));
    const same = (a,b) => JSON.stringify(a) === JSON.stringify(b);
    if (!Array.isArray(records) || records.length > 100 || !Array.isArray(previous)) fail();
    if (new Set(records.map(record => record?.division_id)).size !== records.length || previous.some(record => !records.some(next => same(record,next)))) fail();
    const extended = structuredClone(base), added = new Set();
    const originalMatches = new Map(base.matches.map(match => [match.id,match]));
    for (const record of records) {
        if (!exact(record,['id','division_id','division','source_stage_id','source_match_ids','seed','basis','entry_ids','bracket_entry_ids','match_ids','generated_at']) || !['id','division_id','division','source_stage_id'].every(key => typeof record[key] === 'string' && record[key].length > 0 && record[key].length <= 100) || !Number.isInteger(record.seed) || record.seed < 0 || record.seed > 0xffffffff || typeof record.basis !== 'string' || record.basis.length > 1000000 || !iso(record.generated_at) || !ids(record.source_match_ids) || !record.source_match_ids.length || !ids(record.entry_ids) || !ids(record.match_ids) || !Array.isArray(record.bracket_entry_ids)) fail();
        const division = base.divisions.find(division => division.id === record.division_id);
        const sourceStage = base.stages.find(stage => stage.id === record.source_stage_id);
        if (!division || division.name !== record.division || sourceStage?.division_id !== division.id || sourceStage.type !== 'elimination' || base.stages.some(stage => stage.division_id === division.id && stage.type === 'round_robin') || record.id !== stablePublicId('placement-stage',division.id,'encouragement')) fail();
        const first = base.matches.filter(match => match.division_id === division.id && match.stage_id === sourceStage.id && match.round === 1 && match.kind === 'competitive');
        if (!sameSet(record.source_match_ids,first.map(match => match.id))) fail();
        const qualified = new Map();
        for (const match of first) {
            if ([match.side_a,match.side_b].some(side => side.source_type !== 'entry' || side.resolution !== 'confirmed' || !side.entry_id)) fail();
            const patch = patches.get(match.id);
            if (!patch || patch.status !== 'completed') fail();
            const result = Object.hasOwn(patch,'result') ? scoredResult(patch.result,match) : match.result;
            if (!result || result.outcome === 'retired') fail();
            if (result.outcome === 'played') {
                if (![match.side_a.entry_id,match.side_b.entry_id].includes(result.winner_entry_id)) fail();
                const loser = result.winner_entry_id === match.side_a.entry_id ? match.side_b : match.side_a;
                if (qualified.has(loser.entry_id)) fail();
                qualified.set(loser.entry_id,loser.name);
            } else if (!['walkover','double_forfeit'].includes(result.outcome)) fail();
        }
        if (!sameSet(record.entry_ids,[...qualified.keys()])) fail();
        const n = record.entry_ids.length, capacity = n < 2 ? 0 : 2 ** Math.ceil(Math.log2(n));
        const bracket = record.bracket_entry_ids;
        if (bracket.length !== capacity || n >= 2 && (!sameSet(bracket.filter(id => id !== null),record.entry_ids) || new Set(bracket.filter(id => id !== null)).size !== n) || bracket.some(id => id !== null && !qualified.has(id))) fail();
        if (n < 2 && record.match_ids.length) fail();
        const expected = [], stageId = stablePublicId('placement-stage',division.id,'encouragement');
        const codePrefix = division.name === '國中男子組' ? '國男鼓' : division.name === '國中女子組' ? '國女鼓' : division.name+'鼓';
        let layer = bracket.map(entry => entry === null ? null : {kind:'entry',entry});
        for (let round = 1; layer.length > 1; round++) {
            const next = [];
            for (let position = 0; position < layer.length / 2; position++) {
                const a = layer[2*position], b = layer[2*position+1];
                if (!a && !b) fail();
                if (!a || !b) { if (round !== 1) fail(); next.push(a ?? b); continue; }
                const id = stablePublicId('elimination',stageId,String(round),String(position));
                const side = value => value.kind === 'entry' ? {name:qualified.get(value.entry),entry_id:value.entry,source_type:'entry',source_match_id:null,source_label:null,resolution:'confirmed'} : {name:patches.get(value.match)?.code+'場勝者',entry_id:null,source_type:'match_winner',source_match_id:value.match,source_label:null,resolution:'pending'};
                expected.push({id,code:codePrefix+(expected.length+1),division_id:division.id,stage_id:stageId,round,position,kind:'placement',status:'planned',side_a:side(a),side_b:side(b),result:null,court_id:null,planned_start_at:null,called_at:null,started_at:null,delay_minutes:0,delay_active:false});
                next.push({kind:'winner',match:id});
            }
            layer = next;
        }
        if (expected.length !== Math.max(0,n-1) || !sameSet(record.match_ids,expected.map(match => match.id))) fail();
        const priorRecord = previous.find(prior => prior.id === record.id);
        if (!priorRecord && !reviewed) fail();
        if (expected.length) {
            const stage = {id:stageId,division_id:division.id,name:'鼓勵賽',type:'placement',order:100,bye_names:bracket.flatMap((entry,index) => entry && bracket[index^1] === null ? [qualified.get(entry)] : [])};
            const existingStage = extended.stages.find(stage => stage.id === stageId);
            if (existingStage ? !same(existingStage,stage) : priorRecord) fail();
            if (!existingStage) extended.stages.push(stage);
        }
        for (const expectedMatch of expected) {
            const existing = originalMatches.get(expectedMatch.id), patch = patches.get(expectedMatch.id);
            if (!patch || patch.code !== expectedMatch.code || Object.hasOwn(patch,'result') && patch.result?.rules?.best_of !== 3 || typeof expectedMatch.code !== 'string' || !expectedMatch.code || expectedMatch.code.length > 50 || existing && !priorRecord || !existing && priorRecord || !existing && (patch.status !== 'planned' || Object.hasOwn(patch,'result') || patch.started_at !== null)) fail();
            if (existing) {
                if (['id','code','division_id','stage_id','round','position','kind'].some(key => existing[key] !== expectedMatch[key])) fail();
                for (const key of ['side_a','side_b']) {
                    const from = expectedMatch[key], actual = existing[key];
                    if (['source_type','source_match_id'].some(key => from[key] !== actual[key]) || from.source_type === 'entry' && (from.entry_id !== actual.entry_id || from.name !== actual.name)) fail();
                }
            } else { extended.matches.push(expectedMatch); added.add(expectedMatch.id); }
        }
    }
    if (extended.matches.length > 1000) fail();
    return {base:extended,added};
}

async function cloudPublish(request, env) {
    if (!env.BUCKET)
        return json({ error: 'Storage unavailable' }, 503);
    if (Number(request.headers.get('Content-Length')) > 2000000)
        return json({ error: 'Payload too large' }, 413);
    let length = 0;
    const chunks = [], reader = request.body?.getReader();
    if (reader)
        while (true) {
            const { value, done } = await reader.read();
            if (done)
                break;
            length += value.byteLength;
            if (length > 2000000) {
                await reader.cancel();
                return json({ error: 'Payload too large' }, 413);
            }
            chunks.push(value);
        }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) {
        bytes.set(chunk, offset);
        offset += chunk.byteLength;
    }
    const raw = new TextDecoder().decode(bytes);
    const timestamp = request.headers.get('X-Sync-Time'), signature = request.headers.get('X-Sync-Signature');
    if (!env.CLOUD_PUBLISH_SECRET || !env.PUBLISH_SOURCE_ID || !/^\d{10}$/.test(timestamp || '') || Math.abs(Date.now() / 1000 - Number(timestamp)) > 120 || !/^[a-f0-9]{64}$/.test(signature || ''))
        return json({ error: 'Unauthorized' }, 401);
    const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(env.CLOUD_PUBLISH_SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['verify']);
    if (!await crypto.subtle.verify('HMAC', key, Uint8Array.from(signature.match(/../g), v => parseInt(v, 16)), new TextEncoder().encode(timestamp + '\n/sync/cloud-publish\n' + raw)))
        return json({ error: 'Unauthorized' }, 401);
    let value;
    try {
        value = JSON.parse(raw);
    }
    catch {
        return json({ error: 'Invalid JSON' }, 400);
    }
    if (!exact(value, [...fields, ...(Object.hasOwn(value ?? {},'operation') ? ['operation'] : []), ...(Object.hasOwn(value ?? {},'encouragement_draws') ? ['encouragement_draws'] : [])]) || value.source_id !== env.PUBLISH_SOURCE_ID || !Number.isSafeInteger(value.sequence) || value.sequence < 1 || typeof value.schedule_revision !== 'string' || !value.schedule_revision || !iso(value.created_at) || !iso(value.published_at) || value.live_updated_at !== null && !iso(value.live_updated_at) || Date.parse(value.created_at) > Date.now() + 120000 || !Array.isArray(value.matches))
        return json({ error: 'Invalid publication' }, 400);
    const { object, current } = await readCurrent(env), requestHash = hex(await crypto.subtle.digest('SHA-256', bytes));
    const ack = () => ({ source_id: current.cloud_source_id, sequence: current.cloud_sequence, version: current.descriptor.version });
    if (current?.authority === 'cloud') {
        if (current.cloud_source_id !== value.source_id)
            return json({ error: 'Unexpected source' }, 409);
        if (value.sequence < current.cloud_sequence)
            return json({ ...ack(), error: 'Superseded publication' }, 409);
        if (value.sequence === current.cloud_sequence)
            return current.cloud_request_hash === requestHash ? json(ack()) : json({ error: 'Sequence content mismatch' }, 409);
    }
    const operation = value.operation;
    if (operation && (!exact(operation,['kind','base_sequence']) || !['reviewed-update','restore'].includes(operation.kind) || !Number.isSafeInteger(operation.base_sequence) || operation.base_sequence !== (current?.authority === 'cloud' ? current.cloud_sequence : 0)))
        return json({error:'Publication review is stale'},409);
    const reviewed = !!operation, restore = operation?.kind === 'restore';
    const descriptor = current?.descriptor ?? BOOTSTRAP;
    const stored = await env.BUCKET.get(STORAGE_PREFIX + 'snapshots/' + descriptor.version + '.json');
    const asset = ASSETS[descriptor.snapshot_url];
    let base = stored ? await stored.json() : asset ? JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(asset.body), c => c.charCodeAt(0)))) : null;
    if (!base)
        return json({ error: 'Published snapshot unavailable' }, 503);
    if (restore && base.matches.some(match => match.status === 'in_progress')) return json({error:'Active matches prevent restore'},409);
    const patches = new Map(value.matches.map(match => [match?.id, match]));
    let additions;
    try { additions = extendEncouragement(base,value.encouragement_draws ?? [],current?.cloud_encouragement_draws ?? [],patches,reviewed); }
    catch { return json({error:'Invalid encouragement draw or qualification'},409); }
    base = additions.base;
    const registry = new Map();
    for (const match of base.matches) for (const side of [match.side_a,match.side_b]) if (side.entry_id && side.resolution === 'confirmed') registry.set(match.division_id+'|'+side.entry_id,side.name);
    if (patches.size !== base.matches.length || value.matches.length !== base.matches.length)
        return json({ error: 'Match identity mismatch' }, 409);
    const courts = new Set(base.courts.map(court => court.id)), activeCourts = new Set(), activeEntries = new Set();
    const merged = structuredClone(base);
    for (const match of merged.matches) {
        const patch = patches.get(match.id), division = base.divisions.find(row => row.id === match.division_id);
        const keys = [...matchFields, ...(Object.hasOwn(patch ?? {},'result') ? ['result'] : []), ...(Object.hasOwn(patch ?? {},'side_a_entry_id') ? ['side_a_entry_id','side_b_entry_id'] : [])];
        try { replacePublicSides(match, patch ?? {}, registry, reviewed, restore); } catch { return json({error:'Entry identity mismatch'},409); }
        if (!exact(patch, keys) || patch.code !== match.code || patch.division !== division?.name || typeof patch.side_a !== 'string' || typeof patch.side_b !== 'string' || match.side_a.resolution === 'confirmed' && patch.side_a !== match.side_a.name || match.side_b.resolution === 'confirmed' && patch.side_b !== match.side_b.name || !['planned', 'ready', 'in_progress', 'completed'].includes(patch.status) || patch.court_id !== null && !courts.has(patch.court_id) || patch.planned_start_at !== null && !iso(patch.planned_start_at) || patch.started_at !== null && !iso(patch.started_at) || !!patch.court_id !== !!patch.planned_start_at)
            return json({ error: 'Match identity or field mismatch' }, 409);
        if (Object.hasOwn(patch, 'result')) {
            let result;
            try { result = scoredResult(patch.result, match); }
            catch { return json({ error: 'Invalid scoring result' }, 400); }
            if (patch.status !== 'completed' || !reviewed && match.result && JSON.stringify(match.result) !== JSON.stringify(result))
                return json({ error: 'Result is immutable or match is not completed' }, 409);
            const changed = JSON.stringify(match.result) !== JSON.stringify(result);
            if (changed && match.result && !restore) {
                const descendants = new Set([match.id]); let grew = true;
                while (grew) { grew = false; for (const other of base.matches) if (!descendants.has(other.id) && [other.side_a,other.side_b].some(side => descendants.has(side.source_match_id))) { descendants.add(other.id); grew = true; } }
                if (base.matches.some(other => other.id !== match.id && descendants.has(other.id) && ['in_progress','completed'].includes(other.status))) return json({error:'Downstream match already started'},409);
            }
            match.result = result;
            if (changed) for (const group of merged.standings) {
                const entries = new Set(group.rows.map(row => row.entry_id));
                if (group.stage_id !== match.stage_id || !entries.has(match.side_a.entry_id) || !entries.has(match.side_b.entry_id)) continue;
                group.status = 'manual_review';
                group.notice = '雲端成績已更新；積分與晉級名次待賽務核定。請以各場比賽成績為準。';
                for (const row of group.rows) { row.rank = null; row.qualifies = false; }
            }
        }
        if (restore && !Object.hasOwn(patch,'result')) match.result = null;
        if (match.result && patch.status !== 'completed')
            return json({ error: 'Result would be overwritten' }, 409);
        if (patch.status === 'in_progress') {
            if (!patch.started_at || !patch.court_id || activeCourts.has(patch.court_id) || [match.side_a, match.side_b].some(side => side.resolution !== 'confirmed' || !side.entry_id || activeEntries.has(side.entry_id)))
                return json({ error: 'Invalid active court' }, 409);
            activeCourts.add(patch.court_id);
            activeEntries.add(match.side_a.entry_id);
            activeEntries.add(match.side_b.entry_id);
        }
        Object.assign(match, { status: patch.status, court_id: patch.court_id, planned_start_at: patch.planned_start_at, started_at: patch.started_at, called_at: null, delay_minutes: 0, delay_active: false });
    }
    merged.standings = recalculateStandings(merged, rankingRules).standings;
    merged.revision_id = value.schedule_revision;
    merged.updated_at = value.created_at;
    merged.version = hex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify({ ...merged, version: '' }))));
    try {
        validate(merged);
    }
    catch {
        return json({ error: 'Invalid merged snapshot' }, 400);
    }
    const body = JSON.stringify(merged), snapshotKey = STORAGE_PREFIX + 'snapshots/' + merged.version + '.json', digest = hex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body)));
    const saved = await env.BUCKET.put(snapshotKey, body, { onlyIf: { etagDoesNotMatch: '*' }, customMetadata: { sha256: digest }, httpMetadata: { contentType: 'application/json' } });
    if (!saved && (await env.BUCKET.head(snapshotKey))?.customMetadata?.sha256 !== digest)
        return json({ error: 'Version content mismatch' }, 409);
    const nextDescriptor = Object.fromEntries(['schema_version', 'version', 'workspace_id', 'revision_id', 'updated_at'].map(key => [key, merged[key]]));
    nextDescriptor.snapshot_url = '/public/snapshots/' + merged.version + '.json';
    const now = new Date().toISOString();
    try {
        await writeCurrent(env, { descriptor: nextDescriptor, synced_at: now, last_heartbeat_at: null, authority: 'cloud', cloud_source_id: value.source_id, cloud_sequence: value.sequence, cloud_request_hash: requestHash, cloud_encouragement_draws: value.encouragement_draws ?? [], schedule_published_at: value.published_at, live_updated_at: value.live_updated_at }, object);
    }
    catch {
        return json({ error: 'Concurrent update; retry' }, 409);
    }
    return json({ source_id: value.source_id, sequence: value.sequence, version: merged.version });
}
