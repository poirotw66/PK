import { env } from "cloudflare:workers";
import { headers } from "next/headers";

export function passwordMode() { return env.ADMIN_AUTH_MODE === "password"; }

export function temporaryConfiguration() {
    const encoded = env.TEMP_ADMIN_PASSWORD_HASH ?? "";
    const parts = /^pbkdf2_sha256\$100000\$([a-f0-9]{32})\$([a-f0-9]{64})$/.exec(encoded);
    const expiry = env.TEMP_ADMIN_EXPIRES_AT?.trim();
    const expires = expiry ? Date.parse(expiry) : Infinity;
    if (!passwordMode() || !parts || Number.isNaN(expires) || Date.now() >= expires) return null;
    return { salt: parts[1], hash: parts[2], expires, owner: "temporary-password:" + parts[2] };
}

export function temporaryOwner() { return temporaryConfiguration()?.owner ?? null; }

export async function temporaryIdentity() {
    const config = temporaryConfiguration();
    if (!config || !env.DB) return null;
    const requestHeaders = await headers();
    const raw = requestHeaders.get("Cookie")?.split(";").map(part => part.trim())
        .find(part => part.startsWith("__Host-pk-admin=") || part.startsWith("pk-admin="))?.split("=")[1] ?? "";
    if (!/^[a-f0-9]{64}$/.test(raw)) return null;
    const id = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(raw))), byte => byte.toString(16).padStart(2,"0")).join("");
    const session = await env.DB.prepare("SELECT owner,created,touched FROM sessions WHERE id=?").bind(id)
        .first<{ owner: string; created: number; touched: number }>();
    const now = Date.now();
    if (!session || session.owner !== config.owner || now - session.created >= 28800000 || now - session.touched >= 1800000)
        return null;
    await env.DB.prepare("UPDATE sessions SET touched=? WHERE id=?").bind(now, id).run();
    return { userId: config.owner, displayName: "賽務管理員", email: "密碼登入", fullName: null };
}

export async function verifyTemporaryPassword(password: string) {
    const config = temporaryConfiguration();
    if (!config || !env.DB) return { kind: "unavailable" as const };
    password = password.trim();
    if (password.length < 12 || password.length > 256) return { kind: "invalid" as const };
    const now = Date.now();
    const bucket = "temporary-password-login";
    const attempt = await env.DB.prepare(`INSERT INTO login_attempts (owner,window,count) VALUES (?,?,1)
        ON CONFLICT(owner) DO UPDATE SET window=CASE WHEN login_attempts.window<? THEN excluded.window ELSE login_attempts.window END,
        count=CASE WHEN login_attempts.window<? THEN 1 ELSE login_attempts.count+1 END RETURNING count`)
        .bind(bucket, now, now - 60000, now - 60000).first<{ count: number }>();
    if (!attempt || attempt.count > 5) return { kind: "rate_limited" as const };
    const salt = Uint8Array.from(config.salt.match(/../g) ?? [], part => parseInt(part, 16));
    const material = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveBits"]);
    const bits = await crypto.subtle.deriveBits({ name:"PBKDF2", hash:"SHA-256", salt, iterations:100000 }, material, 256);
    const digest = Array.from(new Uint8Array(bits), byte => byte.toString(16).padStart(2,"0")).join("");
    let difference = 0;
    for (let index = 0; index < digest.length; index++) difference |= digest.charCodeAt(index) ^ config.hash.charCodeAt(index);
    return difference === 0 ? { kind: "ok" as const } : { kind: "invalid" as const };
}
