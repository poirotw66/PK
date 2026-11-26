import { passwordMode, temporaryOwner } from "./temporary-access";
import { env } from "cloudflare:workers";
import { getChatGPTUser } from "../app/chatgpt-auth";
export class HttpError extends Error {
    constructor(public status: number, message: string) { super(message); }
}
export function database(): D1Database {
    if (!env.DB)
        throw new HttpError(503, "資料庫尚未連接，請稍後再試。");
    return env.DB;
}
export function randomToken() { return Array.from(crypto.getRandomValues(new Uint8Array(32)), b => b.toString(16).padStart(2, "0")).join(""); }
export async function digest(value: string) { return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value))), b => b.toString(16).padStart(2, "0")).join(""); }
function equal(a: string, b: string) { if (a.length !== b.length)
    return false; let diff = 0; for (let i = 0; i < a.length; i++)
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i); return diff === 0; }
export async function identity() {
    const user = await getChatGPTUser();
    if (!user)
        throw new HttpError(401, "請先透過 Sites 登入。");
    return user;
}
type Admin = {
    owner: string;
    username: string | null;
};
export async function findAdmin(user: Awaited<ReturnType<typeof identity>>) {
    if (passwordMode()) return user.userId === temporaryOwner() ? { owner:user.userId, kind:"primary" as const, actor:"Password administrator" } : null;
    const db = database();
    const primary = await db.prepare("SELECT owner,username FROM administrator WHERE id=1").first<Admin>();
    const email = user.email.trim().toLowerCase();
    const isPrimary = primary
        ? primary.owner === user.userId
        : email.length > 0 && env.PRIMARY_ADMIN_EMAIL?.trim().toLowerCase() === email;
    if (isPrimary)
        return { owner: user.userId, kind: "primary" as const, actor: primary?.username ?? email };

    const invited = email.length > 0 && (
        env.ADDITIONAL_ADMIN_EMAIL?.trim().toLowerCase() === email
        || env.ADDITIONAL_ADMIN_EMAILS?.split(",").some(entry => entry.trim().toLowerCase() === email)
    );
    if (!primary || !invited)
        return null;
    const record = await db.prepare("SELECT username FROM administrator WHERE id<>1 AND owner=?").bind(user.userId).first<Pick<Admin, "username">>();
    return { owner: user.userId, kind: "additional" as const, actor: record?.username ?? email };
}
export async function adminIdentity() {
    const account = await findAdmin(await identity());
    if (!account)
        throw new HttpError(403, "此帳號沒有管理權限。");
    return account;
}
export function checkOrigin(request: Request) {
    const url = new URL(request.url);
    const local = url.hostname === "127.0.0.1" || url.hostname === "localhost";
    const expected = import.meta.env.DEV && local ? url.origin : env.APP_ORIGIN;
    if (!expected || request.headers.get("Origin") !== expected)
        throw new HttpError(403, "請從操作台送出修改。");
    if (request.headers.get("Sec-Fetch-Site") === "cross-site")
        throw new HttpError(403, "不接受跨網站操作。");
}
function cookieName(request: Request) { return new URL(request.url).protocol === "https:" ? "__Host-pk-admin" : "pk-admin"; }
export function cookie(request: Request, token: string, clear = false) {
    return `${cookieName(request)}=${token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${clear ? 0 : 28800}${new URL(request.url).protocol === "https:" ? "; Secure" : ""}`;
}
export async function readSession(request: Request, owner: string) {
    const raw = request.headers.get("Cookie")?.split(";").map(v => v.trim()).find(v => v.startsWith(cookieName(request) + "="))?.split("=")[1] ?? "";
    if (!/^[a-f0-9]{64}$/.test(raw))
        return null;
    const id = await digest(raw), db = database(), now = Date.now();
    const value = await db.prepare("SELECT owner,csrf,created,touched FROM sessions WHERE id=?").bind(id).first<{
        owner: string;
        csrf: string;
        created: number;
        touched: number;
    }>();
    if (!value || value.owner !== owner || now - value.created >= 28800000 || now - value.touched >= 1800000)
        return null;
    await db.prepare("UPDATE sessions SET touched=? WHERE id=?").bind(now, id).run();
    return { id, csrf: value.csrf };
}
export function checkCsrf(request: Request, csrf: string) {
    if (!equal(csrf, request.headers.get("X-CSRF-Token") ?? ""))
        throw new HttpError(403, "登入驗證已更新，請重新整理。");
}
export async function createSession(request: Request, owner: string) {
    const db = database(), raw = randomToken(), id = await digest(raw), csrf = randomToken(), now = Date.now();
    await db.batch([
        db.prepare("DELETE FROM sessions WHERE created<? OR touched<?").bind(now - 28800000, now - 1800000),
        db.prepare("INSERT INTO sessions (id,owner,csrf,created,touched) VALUES (?,?,?,?,?)").bind(id, owner, csrf, now, now),
    ]);
    return { csrf, setCookie: cookie(request, raw) };
}
