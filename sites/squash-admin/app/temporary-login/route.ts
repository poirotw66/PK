import { redirect } from "next/navigation";
import { checkOrigin, createSession, database, digest, HttpError } from "../../lib/auth";
import { passwordMode, temporaryConfiguration, temporaryIdentity, verifyTemporaryPassword } from "../../lib/temporary-access";

export const dynamic = "force-dynamic";
const responseHeaders = {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
};

function page(failed: boolean, available: boolean, rateLimited = false) {
    return new Response(`<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<script src="/temporary-login.js" defer></script><meta name="viewport" content="width=device-width,initial-scale=1"><title>PK 密碼登入</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f2f4f1;color:#152e29;font:17px/1.7 system-ui,"PingFang TC","Microsoft JhengHei",sans-serif;min-height:100svh;padding:48px 20px;display:grid;place-items:center}main{width:100%;max-width:440px;background:#fff;border:1px solid #d6dfd8;border-radius:18px;padding:36px;box-shadow:0 12px 40px #152e2909;border-top:6px solid #1e503f}main>p:first-child{font-size:16px;font-weight:700;color:#1e503f;border-bottom:1px solid #e1e8dc;padding-bottom:20px;margin:0 0 24px}h1{font-size:32px;line-height:1.3;letter-spacing:-.04em;margin:0 0 16px}p{color:#53665c;margin:12px 0 24px}label{display:block;font-size:15px;font-weight:600}input,button{box-sizing:border-box;width:100%;min-height:52px;font:inherit;padding:12px 14px;margin:8px 0 16px;border:1px solid #aebeaa;border-radius:8px;background:#f9fbf7;color:#152e29}input:focus-visible{outline:3px solid #d8ee92;outline-offset:2px;border-color:#1e503f}button{background:#1e503f;color:white;border-color:#1e503f;font-weight:650;margin:16px 0 0;cursor:pointer;transition:background-color 150ms,transform 150ms}button:hover{background:#143d2d;transform:translateY(-1px)}button:active{transform:translateY(1px)}button:focus-visible{outline:3px solid #74953e;outline-offset:3px}input[type=checkbox]{accent-color:#1e503f;width:20px!important;height:20px;vertical-align:middle;margin-bottom:4px}.error{color:#8c352a;background:#fff1eb;border:1px solid #e6c4b4;border-radius:8px;padding:12px;font-size:15px;margin-bottom:0}@media(max-width:480px){body{padding:24px 16px}main{padding:28px 24px;border-radius:14px}}@media(prefers-reduced-motion:reduce){button{transition:none;transform:none!important}}
</style></head><body><main><p>PK 賽務管理台</p><h1>密碼登入</h1>
${available ? `<p>輸入管理密碼後，擁有完整賽程管理、成績登錄與發布權限。</p><form method="post" action="/temporary-login">
<label for="password">管理密碼</label><input id="password" name="password" type="password" autocomplete="current-password" minlength="12" maxlength="256" autocapitalize="none" autocorrect="off" spellcheck="false" required>
<label style="display:block"><input id="show-password" type="checkbox" style="width:auto;min-height:0;margin-right:8px">顯示密碼</label>
<button type="submit">登入</button></form>` : "<p>密碼入口尚未啟用或已到期，請聯絡管理員。</p>"}
${failed && available ? '<p class="error" role="alert">' + (rateLimited ? '嘗試次數過多，請等待 60 秒後再登入。' : '密碼不正確。可勾選「顯示密碼」檢查大小寫與連字號，再重新輸入。') + '</p>' : ""}</main></body></html>`,
        { status: !available ? 503 : rateLimited ? 429 : failed ? 401 : 200, headers: { ...responseHeaders, ...(rateLimited ? { "Retry-After": "60" } : {}) } });
}

export async function GET() {
    if (!passwordMode()) redirect("/signin-with-chatgpt?return_to=%2F");
    if (await temporaryIdentity()) redirect("/schedule");
    return page(false, !!temporaryConfiguration());
}

export async function POST(request: Request) {
    try {
        if (!passwordMode()) return page(true, false);
        checkOrigin(request);
        if (!request.headers.get("Content-Type")?.startsWith("application/x-www-form-urlencoded"))
            throw new HttpError(415, "Invalid form content type.");
        const reader = request.body?.getReader();
        if (!reader) return page(true, true);
        let length = 0;
        const chunks: Uint8Array[] = [];
        for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            length += value.length;
            if (length > 4096) { await reader.cancel(); throw new HttpError(413, "Login form too large."); }
            chunks.push(value);
        }
        const raw = new Uint8Array(length);
        let offset = 0;
        for (const chunk of chunks) { raw.set(chunk, offset); offset += chunk.length; }
        const form = new URLSearchParams(new TextDecoder().decode(raw));
        const passwords = form.getAll("password");
        if (passwords.length !== 1 || Array.from(form.keys()).some(key => key !== "password"))
            return page(true, !!temporaryConfiguration());
        const verified = await verifyTemporaryPassword(passwords[0]);
        if (verified.kind !== "ok")
            return page(true, verified.kind !== "unavailable", verified.kind === "rate_limited");
        const config = temporaryConfiguration();
        if (!config) return page(true, false);
        const previous = request.headers.get("Cookie")?.split(";").map(value => value.trim())
            .find(value => value.startsWith("__Host-pk-admin=") || value.startsWith("pk-admin="))?.split("=")[1] ?? "";
        if (/^[a-f0-9]{64}$/.test(previous))
            await database().prepare("DELETE FROM sessions WHERE id=?").bind(await digest(previous)).run();
        const session = await createSession(request, config.owner);
        return new Response(null, { status:303, headers:{Location:"/schedule", "Set-Cookie":session.setCookie, "Cache-Control":"no-store"} });
    } catch (error) {
        if (error instanceof HttpError) return new Response("Request rejected.", { status:error.status, headers:{"Cache-Control":"no-store"} });
        return new Response("Login temporarily unavailable.", { status:503, headers:{"Cache-Control":"no-store"} });
    }
}
