import { z } from "zod";
export const scoringRulesSchema = z.object({
    best_of: z.union([z.literal(3), z.literal(5)]),
    target: z.union([z.literal(11), z.literal(15)]),
    margin: z.literal(2),
}).strict();
export const defaultScoringRules = { best_of: 3, target: 11, margin: 2 } as const;
export type ScoringRules = z.infer<typeof scoringRulesSchema>;
export type Side = "a" | "b";
export type Game = { side_a: number; side_b: number };
export type Score = { rules: ScoringRules; points: Side[] };
export const scoreSchema = z.object({ rules: scoringRulesSchema, points: z.array(z.enum(["a", "b"])).max(4000) }).strict();
export const gameSchema = z.object({ side_a: z.number().int().min(0).max(999), side_b: z.number().int().min(0).max(999) }).strict();
export const resultSchema = z.object({
    rules: scoringRulesSchema,
    winner: z.enum(["a", "b"]),
    games: z.array(gameSchema).min(2).max(5),
}).strict();
export type ScoringResult = z.infer<typeof resultSchema>;
export function tally(score: Score) {
    const games: Game[] = [], wins = { a: 0, b: 0 };
    let current: Game = { side_a: 0, side_b: 0 }, winner: Side | null = null;
    for (const side of score.points) {
        if (winner) throw new Error("比賽已分出勝負，不能繼續加分。");
        current[side === "a" ? "side_a" : "side_b"]++;
        if (Math.max(current.side_a, current.side_b) > 999) throw new Error("單局分數超過允許範圍。");
        if (Math.max(current.side_a, current.side_b) >= score.rules.target && Math.abs(current.side_a - current.side_b) >= score.rules.margin) {
            const won: Side = current.side_a > current.side_b ? "a" : "b";
            wins[won]++;
            games.push(current);
            current = { side_a: 0, side_b: 0 };
            if (wins[won] === Math.floor(score.rules.best_of / 2) + 1) winner = won;
        }
    }
    return { games, wins, current, winner };
}
export function scorePoint(score: Score, action: Side | "undo"): Score {
    const next = structuredClone(score);
    if (action === "undo") {
        if (!next.points.length) throw new Error("目前沒有可以復原的得分。");
        next.points.pop();
    } else {
        if (next.points.length >= 4000) throw new Error("計分紀錄已達上限。");
        next.points.push(action);
    }
    tally(next);
    return next;
}
export function validateResult(input: unknown): ScoringResult {
    const result = resultSchema.parse(input), wins = { a: 0, b: 0 }, needed = Math.floor(result.rules.best_of / 2) + 1;
    for (const game of result.games) {
        if (Math.max(wins.a, wins.b) >= needed) throw new Error("勝負確定後不得再增加局數。");
        const high = Math.max(game.side_a, game.side_b), low = Math.min(game.side_a, game.side_b);
        if (high !== Math.max(result.rules.target, low + result.rules.margin)) throw new Error("局分不符合領先兩分的規則。");
        wins[game.side_a > game.side_b ? "a" : "b"]++;
    }
    if (wins[result.winner] !== needed) throw new Error("勝方與局數不一致。");
    return result;
}
export function finishedResult(score: Score): ScoringResult {
    const state = tally(score);
    if (!state.winner) throw new Error("比賽尚未分出勝負，不能登錄完賽。");
    return validateResult({ rules: score.rules, winner: state.winner, games: state.games });
}
