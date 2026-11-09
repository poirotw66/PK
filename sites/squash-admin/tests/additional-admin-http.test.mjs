import assert from "node:assert/strict";
import { mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { Miniflare } from "miniflare";
import { fixture } from "./fixture.mjs";

test("ChatGPT sign-in gives only authorized administrators a session", async t => {
    const origin = "https://pk-admin.example.test";
    const config = JSON.parse(await readFile("dist/server/wrangler.json", "utf8"));
    const bindings = {
        APP_ORIGIN: origin,
        ADDITIONAL_ADMIN_EMAIL: " invited@example.test ",
        ADDITIONAL_ADMIN_EMAILS: " Third@example.test, another@example.test ",
    };
    const server = "dist/server";
    const modules = (await readdir(server, { recursive: true }))
        .filter(file => file.endsWith(".js") && file !== config.main)
        .map(file => ({ type: "ESModule", path: join(server, file) }));
    const persistence = await mkdtemp(join(tmpdir(), "pk-admin-test-"));
    const options = {
        host: "127.0.0.1",
        modulesRoot: server,
        modules: [{ type: "ESModule", path: join(server, config.main) }, ...modules],
        compatibilityDate: config.compatibility_date,
        compatibilityFlags: config.compatibility_flags,
        d1Databases: { DB: "pk-admin-test" },
        d1Persist: persistence,
        bindings: { ...bindings, PRIMARY_ADMIN_EMAIL: "owner@example.test" },
    };
    const runtime = new Miniflare(options);
    t.after(async () => {
        await runtime.dispose();
        await rm(persistence, { recursive: true, force: true });
    });
    const db = await runtime.getD1Database("DB");
    const migrations = (await readdir("drizzle")).filter(file => file.endsWith(".sql")).sort();
    async function migrate(files) {
        for (const file of files) {
            const sql = await readFile(join("drizzle", file), "utf8");
            await db.batch(sql.split("--> statement-breakpoint").map(statement => db.prepare(statement.trim())));
        }
    }
    const owner = { id: "synthetic-owner", email: "owner@example.test" };
    const invited = { id: "synthetic-invited", email: "INVITED@example.test" };
    const third = { id: "synthetic-third", email: "third@example.test" };
    const outsider = { id: "synthetic-outsider", email: "outsider@example.test" };
    async function api(path, user, { body, cookie, csrf, requestOrigin = origin } = {}) {
        const headers = new Headers();
        if (user) {
            headers.set("oai-authenticated-user-id", user.id);
            headers.set("oai-authenticated-user-email", user.email);
        }
        if (cookie) headers.set("Cookie", cookie);
        if (csrf) headers.set("X-CSRF-Token", csrf);
        if (body !== undefined) {
            headers.set("Origin", requestOrigin);
            headers.set("Content-Type", "application/json");
        }
        const response = await runtime.dispatchFetch(origin + "/api/" + path, {
            method: body === undefined ? "GET" : "POST",
            headers,
            body: JSON.stringify(body),
        });
        return {
            status: response.status,
            body: await response.json(),
            cookie: response.headers.get("set-cookie")?.split(";")[0],
        };
    }
    async function expectStatus(status, ...args) {
        const result = await api(...args);
        assert.equal(result.status, status, JSON.stringify(result.body));
        return result;
    }

    await migrate(migrations);
    const blocked = await expectStatus(403, "status", outsider);
    assert.deepEqual(blocked.body.sign_in, { email: outsider.email, sign_out_url: "/signout-with-chatgpt?return_to=%2F" });
    assert.equal(blocked.cookie, undefined);
    assert.equal((await expectStatus(401, "status", null)).body.sign_in, undefined);
    await expectStatus(403, "status", invited);

    const primary = await expectStatus(200, "status", owner);
    assert.equal(primary.body.can_import, true);
    assert.equal(primary.body.imported, false);
    assert.ok(primary.body.csrf);
    await expectStatus(403, "import", owner, { body: fixture(), cookie: primary.cookie });
    await expectStatus(200, "import", owner, { body: fixture(), cookie: primary.cookie, csrf: primary.body.csrf });

    // Simulate the stored accounts from the previous release. The third user has never enrolled.
    await db.batch([
        db.prepare("UPDATE administrator SET username=?, password_hash=? WHERE id=1").bind("legacy-owner", "unused-legacy-hash"),
        db.prepare("INSERT INTO administrator (owner,username,password_hash) VALUES (?,?,?)").bind(invited.id, "legacy-invited", "unused-legacy-hash"),
    ]);
    await runtime.setOptions({ ...options, bindings });

    const ownerStatus = await expectStatus(200, "status", owner, { cookie: primary.cookie });
    assert.equal(ownerStatus.body.imported, true);
    assert.equal(ownerStatus.body.csrf, primary.body.csrf);
    assert.equal(ownerStatus.cookie, undefined);
    await expectStatus(403, "status", { ...owner, id: "different-platform-identity" });

    const secondary = await expectStatus(200, "status", invited);
    assert.equal(secondary.body.can_import, false);
    assert.equal(secondary.body.sign_in.email, invited.email);
    const thirdSession = await expectStatus(200, "status", third);
    assert.equal(thirdSession.body.can_import, false);
    assert.ok(thirdSession.body.csrf);
    await expectStatus(200, "state", third, { cookie: thirdSession.cookie });
    await expectStatus(401, "state", third, { cookie: secondary.cookie });
    await expectStatus(403, "state", outsider, { cookie: thirdSession.cookie });
    await expectStatus(401, "state", null, { cookie: thirdSession.cookie });
    await expectStatus(403, "import", invited, { body: fixture(), cookie: secondary.cookie, csrf: secondary.body.csrf });
    await expectStatus(404, "setup", third, { body: {}, cookie: thirdSession.cookie, csrf: thirdSession.body.csrf });
    await expectStatus(404, "login", third, { body: {}, cookie: thirdSession.cookie, csrf: thirdSession.body.csrf });

    const { body: current } = await expectStatus(200, "state", invited, { cookie: secondary.cookie });
    const updates = current.state.matches.map(match => ({
        id: match.id, start: match.assignment.start + 15, court: match.assignment.court,
    }));
    const draft = { revision: current.revision, updates, reason: "Synthetic administrator update" };
    await expectStatus(403, "preview", invited, { body: draft, cookie: secondary.cookie });
    await expectStatus(403, "preview", invited, {
        body: draft, cookie: secondary.cookie, csrf: secondary.body.csrf, requestOrigin: "https://other.example.test",
    });
    const { body: preview } = await expectStatus(200, "preview", invited, {
        body: draft, cookie: secondary.cookie, csrf: secondary.body.csrf,
    });
    await expectStatus(409, "confirm", third, {
        body: { id: preview.id }, cookie: thirdSession.cookie, csrf: thirdSession.body.csrf,
    });
    await expectStatus(200, "confirm", invited, {
        body: { id: preview.id }, cookie: secondary.cookie, csrf: secondary.body.csrf,
    });
    const { body: updated } = await expectStatus(200, "state", owner, { cookie: primary.cookie });
    assert.equal(updated.history[0].actor, "legacy-invited");

    await expectStatus(200, "logout", third, { body: {}, cookie: thirdSession.cookie, csrf: thirdSession.body.csrf });
    await expectStatus(401, "state", third, { cookie: thirdSession.cookie });
    const renewed = await expectStatus(200, "status", third, { cookie: thirdSession.cookie });
    assert.notEqual(renewed.cookie, thirdSession.cookie);
    await expectStatus(200, "state", third, { cookie: renewed.cookie });

    await runtime.setOptions({
        ...options,
        bindings: { APP_ORIGIN: origin, ADDITIONAL_ADMIN_EMAIL: invited.email },
    });
    await expectStatus(403, "state", third, { cookie: renewed.cookie });
    await expectStatus(200, "state", invited, { cookie: secondary.cookie });
    await expectStatus(200, "state", owner, { cookie: primary.cookie });
});
