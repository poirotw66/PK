import { z } from "zod";
import { database, HttpError } from "./auth";
import type { Tournament } from "./schedule/schema";
import { encouragementStatus, reviewEncouragement } from "./encouragement-model";

const inputSchema=z.object({revision:z.string().min(1).max(100),sequence:z.number().int().nonnegative(),division_id:z.string().min(1).max(100),seed:z.number().int().min(0).max(4294967295).optional()}).strict();
async function current(){
    const db=database(),[main,pub]=await db.batch([db.prepare("SELECT revision,payload FROM tournament WHERE id=1"),db.prepare("SELECT sequence FROM public_state WHERE id=1")]);
    const row=main.results[0] as {revision:string;payload:string}|undefined;if(!row)throw new HttpError(404,"尚未匯入賽程。");
    return {revision:row.revision,state:JSON.parse(row.payload) as Tournament,sequence:(pub.results[0] as {sequence:number}|undefined)?.sequence??0};
}
export async function getEncouragementStatus(){const value=await current();try{return {revision:value.revision,sequence:value.sequence,...encouragementStatus(value.state)};}catch(error){throw new HttpError(409,error instanceof Error?error.message:"鼓勵賽資格無法核對。");}}
async function check(input:unknown,generatedAt:string){
    const request=inputSchema.parse(input),value=await current();if(value.revision!==request.revision||value.sequence!==request.sequence)throw new HttpError(409,"成績或賽程已更新，請重新檢查鼓勵賽資格。");
    try{return {value,request,...reviewEncouragement(value.state,request.division_id,request.seed!,generatedAt)};}catch(error){throw new HttpError(409,error instanceof Error?error.message:"鼓勵賽無法產生。");}
}
export async function previewEncouragement(input:unknown,session:string){
    const request=inputSchema.parse(input);request.seed??=crypto.getRandomValues(new Uint32Array(1))[0];
    const now=Date.now(),generatedAt=new Date(now).toISOString(),reviewed=await check(request,generatedAt),id=crypto.randomUUID(),db=database();
    await db.prepare("DELETE FROM previews WHERE created<?").bind(now-600000).run();
    await db.prepare("INSERT INTO previews(id,session,base,payload,changes,reason,created) VALUES(?,?,?,?,?,?,?)").bind(id,session,request.revision,JSON.stringify({format:"pk-encouragement-preview-v1",request,generated_at:generatedAt}),JSON.stringify(reviewed.changes),"鼓勵賽抽籤與安排",now).run();
    return {id,division:reviewed.division,qualification:reviewed.qualification,draw:reviewed.draw,matches:reviewed.matches,issues:reviewed.issues,changes:reviewed.changes,can_apply:reviewed.can_apply};
}
export async function confirmEncouragement(input:unknown,session:string,actor:string){
    const {id}=z.object({id:z.string().min(1).max(100)}).strict().parse(input),db=database();
    const preview=await db.prepare("SELECT payload FROM previews WHERE id=? AND session=? AND created>?").bind(id,session,Date.now()-600000).first<{payload:string}>();
    if(!preview)throw new HttpError(409,"預覽已到期或已使用，請重新產生鼓勵賽預覽。");
    const saved=JSON.parse(preview.payload);if(saved.format!=="pk-encouragement-preview-v1")throw new HttpError(409,"請使用鼓勵賽專用確認流程。");
    const reviewed=await check(saved.request,saved.generated_at);if(!reviewed.can_apply)throw new HttpError(409,reviewed.issues[0]?.message??"鼓勵賽安排尚未通過檢查。");
    const revision=crypto.randomUUID(),payload=JSON.stringify(reviewed.state),now=Date.now(),audit=JSON.stringify([{kind:"encouragement_draw",draw:reviewed.draw},...reviewed.changes]);
    const result=await db.batch([
        db.prepare("UPDATE tournament SET revision=?,payload=? WHERE id=1 AND revision=? AND COALESCE((SELECT sequence FROM public_state WHERE id=1),0)=?").bind(revision,payload,reviewed.value.revision,reviewed.value.sequence),
        db.prepare("INSERT INTO revisions(id,parent,actor,reason,created,payload,changes) SELECT ?,?,?,?,?,?,? FROM tournament WHERE id=1 AND revision=?").bind(revision,reviewed.value.revision,actor,`鼓勵賽：${reviewed.division} 抽籤並安排 ${reviewed.matches.length} 場`,now,payload,audit,revision),
        db.prepare("DELETE FROM previews WHERE id=? AND session=? AND EXISTS(SELECT 1 FROM tournament WHERE id=1 AND revision=?)").bind(id,session,revision),
    ]);
    if(result[0].meta.changes!==1)throw new HttpError(409,"另一位管理員已修改成績或賽程，請重新產生鼓勵賽預覽。");
    return {ok:true,revision,requires_publication:true};
}
