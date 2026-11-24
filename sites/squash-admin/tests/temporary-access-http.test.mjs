import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { pbkdf2Sync } from "node:crypto";
import test from "node:test";
import { Miniflare } from "miniflare";
import { fixture } from "./fixture.mjs";

test("temporary password replaces platform identity and preserves session, CSRF, expiry and logout boundaries", async t => {
    const origin = "https://temporary.example.test";
    const password = "synthetic-password-only";
    const hash = "pbkdf2_sha256$100000$" + "01".repeat(16) + "$"
        + pbkdf2Sync(password, Buffer.alloc(16,1),100000,32,"sha256").toString("hex");
    const config = JSON.parse(await readFile("dist/server/wrangler.json","utf8"));
    const modules = (await readdir("dist/server",{recursive:true})).filter(file => file.endsWith(".js") && file !== config.main)
        .map(file => ({type:"ESModule",path:join("dist/server",file)}));
    const options = {
        modulesRoot:"dist/server", modules:[{type:"ESModule",path:join("dist/server",config.main)},...modules],
        compatibilityDate:config.compatibility_date,compatibilityFlags:config.compatibility_flags,
        d1Databases:{DB:"temporary-test"},
        bindings:{APP_ORIGIN:origin,PRIMARY_ADMIN_EMAIL:"owner@example.test",ADMIN_AUTH_MODE:"password",
            TEMP_ADMIN_PASSWORD_HASH:hash,TEMP_ADMIN_EXPIRES_AT:new Date(Date.now()+86400000).toISOString()},
    };
    const runtime = new Miniflare(options);
    t.after(()=>runtime.dispose());
    const db=await runtime.getD1Database("DB");
    for(const file of (await readdir("drizzle")).filter(file=>file.endsWith(".sql")).sort())
        await db.batch((await readFile(join("drizzle",file),"utf8")).split("--> statement-breakpoint").map(sql=>db.prepare(sql.trim())));
    await db.prepare("INSERT INTO administrator (id,owner) VALUES (1,'original-owner')").run();
    await db.prepare("INSERT INTO tournament VALUES (1,'original-revision',?)").bind(JSON.stringify(fixture())).run();

    const request=(path, options={})=>runtime.dispatchFetch(origin+path,{redirect:"manual",...options});
    const login=(value,passwordOrigin=origin)=>request("/temporary-login",{method:"POST",headers:{Origin:passwordOrigin,"Content-Type":"application/x-www-form-urlencoded"},body:new URLSearchParams({password:value}).toString()});
    for(const path of ["/","/live","/schedule","/ipad"]) {
        const response=await request(path);
        assert.equal(response.status,307);
        assert.equal(new URL(response.headers.get("location"),origin).pathname,"/temporary-login");
    }
    assert.equal((await request("/api/status",{headers:{"oai-authenticated-user-id":"original-owner","oai-authenticated-user-email":"owner@example.test"}})).status,401);
    const loginPage = await request("/temporary-login");
    assert.equal(loginPage.headers.get("Referrer-Policy"), "same-origin");
    assert.equal((await login(password,"null")).status,403);
    assert.equal((await login(password,"https://attacker.example")).status,403);
    assert.equal((await login("synthetic-wrong-password")).status,401);
    const signed=await login("  " + password + "  ");
    assert.equal(signed.status,303);
    const cookie=signed.headers.get("set-cookie").split(";")[0];
    assert.match(signed.headers.get("set-cookie"),/Secure/);
    assert.match(signed.headers.get("set-cookie"),/HttpOnly/);
    const authorized=path=>request(path,{headers:{Cookie:cookie}});
    assert.equal((await authorized("/ipad")).status,200);
    const status=await (await authorized("/api/status")).json();
    assert.equal(status.imported,true);
    assert.equal(status.can_import,false);
    assert.equal(status.sign_in.sign_out_url,"/temporary-login");
    const post=(path,csrf)=>request("/api/"+path,{method:"POST",headers:{Cookie:cookie,Origin:origin,"Content-Type":"application/json","X-CSRF-Token":csrf},body:"{}"});
    assert.equal((await post("publish","")).status,403);
    assert.equal((await post("import",status.csrf)).status,403);
    assert.equal((await (await authorized("/api/state")).json()).revision,"original-revision");
    assert.equal((await db.prepare("SELECT owner FROM administrator WHERE id=1").first()).owner,"original-owner");
    assert.equal((await post("logout",status.csrf)).status,200);
    assert.equal((await authorized("/api/state")).status,401);
    for(let index=0;index<3;index++) assert.equal((await login("synthetic-wrong-password")).status,401);
    assert.equal((await login(password)).status,429);
    await db.prepare("DELETE FROM login_attempts").run();
    const next=await login(password);
    const nextCookie=next.headers.get("set-cookie").split(";")[0];
    await runtime.setOptions({...options,bindings:{...options.bindings,TEMP_ADMIN_EXPIRES_AT:"2020-01-01T00:00:00Z"}});
    assert.equal((await request("/api/state",{headers:{Cookie:nextCookie}})).status,401);
    assert.equal((await request("/temporary-login")).status,503);
});
