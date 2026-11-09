// The updater reuses the existing Sites service credential; only its hash is configured here.
export async function validSyncServiceToken(header: string | null, expectedHash: string | undefined): Promise<boolean> {
    if (!expectedHash || !/^[a-f0-9]{64}$/.test(expectedHash)) return false;
    const token = /^Bearer ([^\s]{16,4096})$/.exec(header ?? "")?.[1];
    if (!token) return false;
    const actual = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token))), value => value.toString(16).padStart(2,"0")).join("");
    let difference = 0;
    for (let index = 0; index < actual.length; index++) difference |= actual.charCodeAt(index) ^ expectedHash.charCodeAt(index);
    return difference === 0;
}
