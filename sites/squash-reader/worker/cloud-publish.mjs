const fields = ['source_id', 'sequence', 'schedule_revision', 'published_at', 'live_updated_at', 'created_at', 'matches'];
const matchFields = ['id', 'code', 'division', 'side_a', 'side_b', 'status', 'court_id', 'planned_start_at', 'started_at'];
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const iso = value => typeof value === 'string' && Number.isFinite(Date.parse(value)) && new Date(value).toISOString() === value;
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
    if (!exact(value, fields) || value.source_id !== env.PUBLISH_SOURCE_ID || !Number.isSafeInteger(value.sequence) || value.sequence < 1 || typeof value.schedule_revision !== 'string' || !value.schedule_revision || !iso(value.created_at) || !iso(value.published_at) || value.live_updated_at !== null && !iso(value.live_updated_at) || Date.parse(value.created_at) > Date.now() + 120000 || !Array.isArray(value.matches))
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
    const descriptor = current?.descriptor ?? BOOTSTRAP;
    const stored = await env.BUCKET.get('snapshots/' + descriptor.version + '.json');
    const asset = ASSETS[descriptor.snapshot_url];
    const base = stored ? await stored.json() : asset ? JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(asset.body), c => c.charCodeAt(0)))) : null;
    if (!base)
        return json({ error: 'Published snapshot unavailable' }, 503);
    const patches = new Map(value.matches.map(match => [match?.id, match]));
    if (patches.size !== base.matches.length || value.matches.length !== base.matches.length)
        return json({ error: 'Match identity mismatch' }, 409);
    const courts = new Set(base.courts.map(court => court.id)), activeCourts = new Set(), activeEntries = new Set();
    const merged = structuredClone(base);
    for (const match of merged.matches) {
        const patch = patches.get(match.id), division = base.divisions.find(row => row.id === match.division_id);
        if (!exact(patch, matchFields) || patch.code !== match.code || patch.division !== division?.name || typeof patch.side_a !== 'string' || typeof patch.side_b !== 'string' || match.side_a.resolution === 'confirmed' && patch.side_a !== match.side_a.name || match.side_b.resolution === 'confirmed' && patch.side_b !== match.side_b.name || !['planned', 'ready', 'in_progress', 'completed'].includes(patch.status) || patch.court_id !== null && !courts.has(patch.court_id) || patch.planned_start_at !== null && !iso(patch.planned_start_at) || patch.started_at !== null && !iso(patch.started_at) || !!patch.court_id !== !!patch.planned_start_at)
            return json({ error: 'Match identity or field mismatch' }, 409);
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
    merged.revision_id = value.schedule_revision;
    merged.updated_at = value.created_at;
    merged.version = hex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify({ ...merged, version: '' }))));
    try {
        validate(merged);
    }
    catch {
        return json({ error: 'Invalid merged snapshot' }, 400);
    }
    const body = JSON.stringify(merged), snapshotKey = 'snapshots/' + merged.version + '.json', digest = hex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body)));
    const saved = await env.BUCKET.put(snapshotKey, body, { onlyIf: { etagDoesNotMatch: '*' }, customMetadata: { sha256: digest }, httpMetadata: { contentType: 'application/json' } });
    if (!saved && (await env.BUCKET.head(snapshotKey))?.customMetadata?.sha256 !== digest)
        return json({ error: 'Version content mismatch' }, 409);
    const nextDescriptor = Object.fromEntries(['schema_version', 'version', 'workspace_id', 'revision_id', 'updated_at'].map(key => [key, merged[key]]));
    nextDescriptor.snapshot_url = '/public/snapshots/' + merged.version + '.json';
    const now = new Date().toISOString();
    try {
        await writeCurrent(env, { descriptor: nextDescriptor, synced_at: now, last_heartbeat_at: null, authority: 'cloud', cloud_source_id: value.source_id, cloud_sequence: value.sequence, cloud_request_hash: requestHash, schedule_published_at: value.published_at, live_updated_at: value.live_updated_at }, object);
    }
    catch {
        return json({ error: 'Concurrent update; retry' }, 409);
    }
    return json({ source_id: value.source_id, sequence: value.sequence, version: merged.version });
}
