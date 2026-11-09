import test from 'node:test';
import assert from 'node:assert/strict';
import {validSyncServiceToken} from '../outputs/sync-service-auth.mjs';
const token='synthetic-unattended-sync-service-token';
const hash=Buffer.from(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(token))).toString('hex');
test('service authorization requires the configured credential without browser identity or cookies',async()=>{
 assert.equal(await validSyncServiceToken('Bearer '+token,hash),true);
 for(const header of [null,'Bearer wrong-token-long-enough','Basic '+token,'Bearer '+token+' extra','Bearer tiny'])assert.equal(await validSyncServiceToken(header,hash),false);
 assert.equal(await validSyncServiceToken('Bearer '+token,undefined),false);assert.equal(await validSyncServiceToken('Bearer '+token,'invalid'),false);
});
