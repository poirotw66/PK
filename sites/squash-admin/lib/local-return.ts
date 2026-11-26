import { database, HttpError } from "./auth";
import { buildCloudReturn } from "./local-return-model";
import type { Tournament } from "./schedule/schema";
import type { PublicationPayload, PublicEnvelope } from "./publication-model";

export async function exportLocalReturn(origin: string) {
    const db = database();
    const [canonical, publication, history] = await db.batch([
        db.prepare("SELECT revision,payload FROM tournament WHERE id=1"),
        db.prepare("SELECT sequence,payload FROM public_state WHERE id=1"),
        db.prepare("SELECT payload FROM public_outbox WHERE action='start' ORDER BY sequence"),
    ]);
    const main = canonical.results[0] as { revision: string; payload: string } | undefined;
    if (!main) throw new HttpError(404, "尚未匯入賽程。");
    const published = publication.results[0] as { sequence: number; payload: string } | undefined;
    try {
        return await buildCloudReturn({
            state: JSON.parse(main.payload) as Tournament,
            cloud_revision: main.revision,
            origin,
            exported_at: new Date().toISOString(),
            sequence: published?.sequence ?? 0,
            live: published ? (JSON.parse(published.payload) as PublicationPayload).live : null,
            start_history: (history.results as { payload: string }[]).map(row => JSON.parse(row.payload) as PublicEnvelope),
        });
    } catch (error) {
        throw new HttpError(409, error instanceof Error ? error.message : "目前無法建立地端交接檔。");
    }
}
