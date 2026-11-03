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
export async function passwordHash(password: string, salt: string): Promise<string> {
    if (!env.ADMIN_PEPPER)
        throw new HttpError(503, "登入設定尚未完成。");
    const encoder = new TextEncoder();
    const pepper = await crypto.subtle.importKey("raw", encoder.encode(env.ADMIN_PEPPER), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const input = await crypto.subtle.sign("HMAC", pepper, encoder.encode(password));
    const key = await crypto.subtle.importKey("raw", input, "PBKDF2", false, ["deriveBits"]);
    // Workerd caps PBKDF2 at 100,000 iterations. The pepper is a separate Sites secret.
    const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-512", salt: encoder.encode(salt), iterations: 100000 }, key, 256);
    return Array.from(new Uint8Array(bits), b => b.toString(16).padStart(2, "0")).join("");
}
export async function identity() {
    const user = await getChatGPTUser();
    if (!user)
        throw new HttpError(401, "請先透過 Sites 登入。");
    return user.userId;
}
export type Admin = {
    owner: string;
    username: string | null;
    password_hash: string | null;
};
export async function admin() { return database().prepare("SELECT owner,username,password_hash FROM administrator WHERE id=1").first<Admin>(); }
export async function ownerIdentity() {
    const owner = await identity(), record = await admin();
    if (record && record.owner !== owner)
        throw new HttpError(403, "此帳號沒有管理權限。");
    return { owner, record };
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
export async function session(request: Request, write = false) {
    const { owner, record } = await ownerIdentity();
    if (!record?.password_hash)
        throw new HttpError(401, "請先設定管理員帳密。");
    const raw = request.headers.get("Cookie")?.split(";").map(v => v.trim()).find(v => v.startsWith(cookieName(request) + "="))?.split("=")[1] ?? "";
    if (!/^[a-f0-9]{64}$/.test(raw))
        throw new HttpError(401, "請登入管理員帳號。");
    const id = await digest(raw), db = database(), now = Date.now();
    const value = await db.prepare("SELECT owner,csrf,created,touched FROM sessions WHERE id=?").bind(id).first<{
        owner: string;
        csrf: string;
        created: number;
        touched: number;
    }>();
    if (!value || value.owner !== owner || now - value.created >= 28800000 || now - value.touched >= 1800000)
        throw new HttpError(401, "登入已到期，請重新登入。");
    if (write && !equal(value.csrf, request.headers.get("X-CSRF-Token") ?? ""))
        throw new HttpError(403, "登入驗證已更新，請重新整理。");
    await db.prepare("UPDATE sessions SET touched=? WHERE id=?").bind(now, id).run();
    return { id, owner, csrf: value.csrf, username: record.username! };
}
export async function createSession(request: Request, owner: string) {
    const db = database(), raw = randomToken(), id = await digest(raw), csrf = randomToken(), now = Date.now();
    await db.batch([
        db.prepare("DELETE FROM sessions WHERE created<? OR touched<?").bind(now - 28800000, now - 1800000),
        db.prepare("INSERT INTO sessions (id,owner,csrf,created,touched) VALUES (?,?,?,?,?)").bind(id, owner, csrf, now, now),
    ]);
    return cookie(request, raw);
}
export async function verifyPassword(username: string, password: string, record: Admin | null) {
    const [salt, expected] = record?.password_hash?.split(":") ?? ["synthetic-unknown-salt", "0".repeat(64)];
    const actual = await passwordHash(password, salt);
    return equal(actual, expected) && equal(username, record?.username ?? "");
}
