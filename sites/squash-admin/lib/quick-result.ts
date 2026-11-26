import { z } from "zod";
import { database,HttpError } from "./auth";
import { canonicalJson } from "./local-return-model";
import { resultSchema } from "./scoring";
import type { ScoringResult } from "./scoring";
import type { Tournament } from "./schedule/schema";
import { commitReviewedResult,controlState,flushPublication,publicationRow } from "./publication";
import { readScore } from "./score-storage";
const normalized=(result:ScoringResult|null)=>result?{...result,outcome:result.outcome??"played"}:null;
export async function quickResult(input:unknown,actor:string,canCorrect:boolean) {
    const request=z.object({revision:z.string().min(1).max(100),sequence:z.number().int().nonnegative(),match_id:z.string().min(1).max(100),result:resultSchema,expected_result:resultSchema.nullable()}).strict().parse(input);
    const main=await database().prepare("SELECT revision,payload FROM tournament WHERE id=1").first<{revision:string;payload:string}>(),publication=await publicationRow();
    if(!main||!publication||main.revision!==request.revision||publication.sequence!==request.sequence)throw new HttpError(409,"資料剛被更新，輸入已保留，請重新核對後儲存。");
    const match=(JSON.parse(main.payload) as Tournament).matches.find(match=>match.id===request.match_id);
    if(!match)throw new HttpError(404,"找不到場次。");
    if(match.result&&!canCorrect)throw new HttpError(403,"只有主管理員可更正成績。");
    if(canonicalJson(normalized(match.result??null))!==canonicalJson(normalized(request.expected_result)))throw new HttpError(409,"這場成績已由其他人修改，請重新核對，不能覆蓋。");
    if(!["played","walkover"].includes(request.result.outcome??"played"))throw new HttpError(400,"快速登錄僅接受每局比分或選手棄賽。");
    const scoreVersion=match.status==="in_progress"?(await readScore(match.id)).version:undefined;
    await commitReviewedResult(request.revision,request.sequence,request.match_id,request.result,match.result?"成績表更正":"成績表登錄",actor,scoreVersion);
    await flushPublication();
    return controlState();
}
