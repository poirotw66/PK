import { z } from "zod";
import { readTournament, tournamentSchema } from "./schedule/schema";
import type { Tournament } from "./schedule/schema";
import { validate } from "./schedule/rules";
import { canonicalDigest, canonicalJson } from "./local-return-model";

export const localUpdateSchema = z.object({
    format: z.literal("pk-local-update-v1"),
    base_cloud_revision: z.string().min(1).max(100),
    base_source_revision: z.string().min(1).max(100),
    origin: z.string().url().max(500),
    base_sequence: z.number().int().nonnegative(),
    state: tournamentSchema,
    sha256: z.string().regex(/^[a-f0-9]{64}$/),
}).strict();
export type LocalUpdate = z.infer<typeof localUpdateSchema>;

export async function reviewLocalUpdate(current: Tournament, input: unknown, origin: string) {
    const update = localUpdateSchema.parse(input);
    const { sha256, ...payload } = update;
    if (await canonicalDigest(payload) !== sha256) throw new Error("地端交接檔校驗失敗，請重新匯出。");
    if (update.origin !== origin) throw new Error("地端交接檔屬於另一個雲端網站。");
    if (update.base_source_revision !== current.source_revision) throw new Error("地端交接檔的來源版本已過期，請重新取回雲端資料。");
    if (current.matches.some(match => match.status === "in_progress")) throw new Error("仍有比賽進行中，不能匯入地端重排。");
    const next = readTournament(update.state);
    const before = structuredClone(current), after = structuredClone(next);
    after.source_revision = before.source_revision;
    if (before.encouragement_sources && after.encouragement_sources) after.encouragement_sources.source_revision=before.encouragement_sources.source_revision;
    if (next.source_revision === current.source_revision) throw new Error("請匯出新的地端賽事版本。");
    const profile = next.coach_profile;
    if (profile) {
        if (new Set(profile.championship_ids).size !== profile.championship_ids.length || profile.championship_ids.some(id => !next.matches.some(match => match.id === id))) throw new Error("冠軍賽設定不完整。");
        for (const match of after.matches) {
            const prior = before.matches.find(row => row.id === match.id);
            if (!prior || prior.championship !== undefined && match.championship !== prior.championship || match.championship !== profile.championship_ids.includes(match.id)) throw new Error("冠軍賽設定與場次標記不一致。");
            const expected = prior.status === "completed" || prior.status === "in_progress" ? prior.scoring_rules : { ...(prior.scoring_rules ?? {target:11,margin:2}), best_of: match.championship ? 5 : 3 };
            if (canonicalJson(match.scoring_rules) !== canonicalJson(expected)) throw new Error("地端計分規則不符合已核定冠軍賽設定。");
            if (prior.championship === undefined) delete match.championship; else match.championship = prior.championship;
            if (prior.scoring_rules === undefined) delete match.scoring_rules; else match.scoring_rules = structuredClone(prior.scoring_rules);
        }
        if (before.coach_profile === undefined) delete after.coach_profile; else after.coach_profile = structuredClone(before.coach_profile);
    }
    for (const match of before.matches) {
        const candidate = after.matches.find(row => row.id === match.id);
        if (!candidate) throw new Error("地端重排不得新增或刪除場次。");
        if (match.allowed_outcomes) candidate.allowed_outcomes = structuredClone(match.allowed_outcomes); else delete candidate.allowed_outcomes;
        if (match.status === "planned" || match.status === "ready") {
            if (match.lock_time && match.assignment?.start !== candidate.assignment?.start
                || match.lock_court && match.assignment?.court !== candidate.assignment?.court)
                throw new Error("地端重排不能修改鎖定的安排。");
            candidate.assignment = structuredClone(match.assignment);
        }
    }
    if (canonicalJson(before) !== canonicalJson(after))
        throw new Error("地端交接只能調整未開賽時間、場地與已核定特殊結果類型；選手、計分規則、成績及既有場次必須保持一致。");
    const issues = validate(next);
    if (issues.length) throw new Error("地端重排未通過賽程檢查。" + issues[0].message);
    const changes = next.matches.flatMap(match => {
        const prior = current.matches.find(row => row.id === match.id)!;
        return canonicalJson(prior.assignment) === canonicalJson(match.assignment) ? [] : [{
            id: match.id, code: match.code, before: prior.assignment, after: match.assignment,
        }];
    });
    const policy_changes = next.matches.flatMap(match => {
        const prior = current.matches.find(row => row.id === match.id)!;
        const before = prior.allowed_outcomes ?? ["played"], after = match.allowed_outcomes ?? ["played"];
        return canonicalJson(before) === canonicalJson(after) ? [] : [{ id: match.id, code: match.code, before, after }];
    });
    return { update, state: next, changes, policy_changes };
}
