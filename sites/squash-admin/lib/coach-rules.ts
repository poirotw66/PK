import { z } from "zod";
import { database,HttpError } from "./auth";
import type { Tournament } from "./schedule/schema";
import { reviewCoachRules } from "./coach-rules-model";

const requestSchema=z.object({revision:z.string().min(1).max(100),sequence:z.number().int().nonnegative(),championship_ids:z.array(z.string().min(1).max(100)).max(1000)}).strict();
async function check(input:unknown) {
    const request=requestSchema.parse(input),db=database();
    const [canonical,published]=await db.batch([db.prepare("SELECT revision,payload FROM tournament WHERE id=1"),db.prepare("SELECT sequence FROM public_state WHERE id=1")]);
    const main=canonical.results[0] as {revision:string;payload:string}|undefined,publication=published.results[0] as {sequence:number}|undefined;
    if(!main)throw new HttpError(404,"尚未匯入賽程。");
    const sequence=publication?.sequence??0;
    if(main.revision!==request.revision||sequence!==request.sequence)throw new HttpError(409,"賽程或成績已更新，請重新預覽賽制。");
    try{return {request,...reviewCoachRules(JSON.parse(main.payload) as Tournament,request.championship_ids)};}
    catch(error){throw new HttpError(409,error instanceof Error?error.message:"賽制設定無效。");}
}
export async function previewCoachRules(input:unknown,session:string) {
    const reviewed=await check(input),id=crypto.randomUUID(),now=Date.now(),db=database();
    await db.prepare("DELETE FROM previews WHERE created<?").bind(now-600000).run();
    await db.prepare("INSERT INTO previews(id,session,base,payload,changes,reason,created) VALUES(?,?,?,?,?,?,?)").bind(id,session,reviewed.request.revision,JSON.stringify({format:"pk-coach-rules-preview-v1",request:reviewed.request}),JSON.stringify(reviewed.changes),"教練賽制設定",now).run();
    return {id,changes:reviewed.changes,can_apply:true};
}
export async function confirmCoachRules(input:unknown,session:string,actor:string) {
    const {id}=z.object({id:z.string().min(1).max(100)}).strict().parse(input),db=database();
    const plan=await db.prepare("SELECT payload FROM previews WHERE id=? AND session=? AND created>?").bind(id,session,Date.now()-600000).first<{payload:string}>();
    if(!plan)throw new HttpError(409,"預覽已到期或已使用，請重新預覽賽制。");
    const saved=JSON.parse(plan.payload);
    if(saved.format!=="pk-coach-rules-preview-v1")throw new HttpError(409,"請使用賽制設定的專用確認流程。");
    const reviewed=await check(saved.request),revision=crypto.randomUUID(),payload=JSON.stringify(reviewed.state),now=Date.now();
    const audit=[{policy:"coach-v1",championship_ids:reviewed.state.coach_profile!.championship_ids},...reviewed.changes];
    const updates=await db.batch([
        db.prepare("UPDATE tournament SET revision=?,payload=? WHERE id=1 AND revision=? AND COALESCE((SELECT sequence FROM public_state WHERE id=1),0)=?").bind(revision,payload,reviewed.request.revision,reviewed.request.sequence),
        db.prepare("INSERT INTO revisions(id,parent,actor,reason,created,payload,changes) SELECT ?,?,?,?,?,?,? FROM tournament WHERE id=1 AND revision=?").bind(revision,reviewed.request.revision,actor,"教練賽制設定：冠軍賽五局三勝，其餘三局兩勝；允許選手棄賽",now,payload,JSON.stringify(audit),revision),
        db.prepare("DELETE FROM previews WHERE id=? AND session=? AND EXISTS(SELECT 1 FROM tournament WHERE id=1 AND revision=?)").bind(id,session,revision),
    ]);
    if(updates[0].meta.changes!==1)throw new HttpError(409,"賽程或成績已更新，請重新預覽賽制。");
    return {ok:true,revision,requires_publication:true};
}
