import { z } from "zod";
import { database, HttpError } from "./auth";
import { controlState, publicationRow } from "./publication";
import type { Tournament } from "./schedule/schema";
import type { PublicationPayload } from "./publication-model";
import { planDispatch } from "./dispatch-model";
export async function saveDispatch(input: unknown, actor: string) {
    const value=z.object({revision:z.string().min(1).max(100),sequence:z.number().int().nonnegative(),match_id:z.string().min(1).max(100),court_id:z.string().min(1).max(100).nullable(),position:z.number().int().min(0).max(3).optional()}).strict().parse(input);
    const db=database(),main=await db.prepare("SELECT revision,payload FROM tournament WHERE id=1").first<{revision:string;payload:string}>(),published=await publicationRow();
    if(!main||!published||main.revision!==value.revision||published.sequence!==value.sequence)throw new HttpError(409,"賽程已更新，請重新整理派場資料。");
    const state=JSON.parse(main.payload) as Tournament,publication=JSON.parse(published.payload) as PublicationPayload;
    if(value.court_id&&publication.live.courts[value.court_id]?.match_id&&publication.live.courts[value.court_id].match_id!==value.match_id)throw new HttpError(409,"此場地尚有未結案的舊開賽紀錄，請先登錄該場成績。");
    const before=publication.live.queues??{};
    try{publication.live.queues=planDispatch(state,before,value.match_id,value.court_id,value.position);}catch(error){throw new HttpError(409,error instanceof Error?error.message:"無法派場。");}
    if(JSON.stringify(before)===JSON.stringify(publication.live.queues))return controlState();
    const revision=crypto.randomUUID(),changes=JSON.stringify([{kind:"dispatch",match_id:value.match_id,before,after:publication.live.queues}]);
    const result=await db.batch([
        db.prepare("UPDATE tournament SET revision=? WHERE id=1 AND revision=? AND EXISTS(SELECT 1 FROM public_state WHERE id=1 AND sequence=?)").bind(revision,main.revision,published.sequence),
        db.prepare("UPDATE public_state SET payload=? WHERE id=1 AND sequence=? AND EXISTS(SELECT 1 FROM tournament WHERE id=1 AND revision=?)").bind(JSON.stringify(publication),published.sequence,revision),
        db.prepare("INSERT INTO revisions(id,parent,actor,reason,created,payload,changes) SELECT ?,?,?,?,?,?,? FROM tournament WHERE id=1 AND revision=?").bind(revision,main.revision,actor,"Court queue assignment",Date.now(),main.payload,changes,revision),
    ]);
    if(result[0].meta.changes!==1)throw new HttpError(409,"另一位管理員剛修改派場，請重新整理後重試。");
    return controlState();
}
