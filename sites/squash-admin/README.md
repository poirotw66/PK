# PK schedule administration

Private Sites application for adjusting an existing squash tournament schedule.
The Python solver and PDF exports remain local. This application never calls them.

## Supported flow

1. Open the administration Site and sign in with an authorized ChatGPT account.
2. Have the primary administrator import an exported schedule once.
3. Select a match in the court board, then click a green available position or a
   nearby suggestion. Repeat for other matches; undo supports the last 20 draft
   changes. The list/manual editor remains available.
4. Enter a reason and review all conflicts and before/after changes.
5. Confirm the immutable preview. A D1 transaction saves a new revision and history.

The primary administrator retains exclusive access to the initial schedule import
and is bound to the stored Sites user ID. On a new database, configure
`PRIMARY_ADMIN_EMAIL` before deploying to allow that account to import the first
schedule and establish the binding. To authorize additional administrators, set a
comma-separated `ADDITIONAL_ADMIN_EMAILS` list in the Sites runtime before
deploying. The existing `ADDITIONAL_ADMIN_EMAIL` setting also authorizes its single
address. The application checks each administrator's current Sites identity on
every protected request. Removing an address from the runtime settings in a later
deployment blocks that account's access.

Schedule edits require explicit publication to the separate spectator Site. Court
starts and confirmed match results synchronize automatically through the signed
publication outbox. They do not modify the original local workspace or old PDF
exports. Automatic re-planning remains outside this version.

## Local and cloud handoff

The primary administrator can download a **地端交接檔** from the schedule page.
This authenticated download contains the current canonical schedule, confirmed
results, original local source revision, cloud revision, publication sequence,
actual start times and actual courts. It excludes session tokens and point history.
Finish active matches and confirm their results before downloading. A completed
match without a result, or a historical actual start without court evidence, blocks
the download with an explanation. Additional and temporary administrators cannot
download or replace a local handoff.

Import the `pk-cloud-return-v1` file through the local operator's workflow page,
review the differences, then confirm. After replanning locally, export the
`pk-local-update-v1` handoff and select it on this Site's schedule page. Enter a
reason, preview, and confirm the replacement. Only time/court changes to existing
unstarted, unlocked matches are allowed. Participants, rules, match topology and
confirmed results remain fixed. Changed advancement identities require a separate
reviewed workflow and are rejected by this transfer.

The replacement requires the same Site origin, original source revision, cloud
revision and publication sequence captured by the return file. If cloud operations
occur after download or preview, the replacement fails without overwriting them.
Confirmation records a new cloud revision and keeps the existing public version;
publish separately after reviewing the result. SHA-256 checks file integrity, not
the sender's identity. Keep handoff files private and transfer them only through
the authenticated operator interfaces.

Bundle `lib/local-return-model.ts` and `lib/local-update-model.ts` into
`outputs/local-return-model.mjs` and `outputs/local-update-model.mjs` alongside the
existing `rules` and `publication-model` bundles. Run
`node --test tests/local-handoff.test.mjs`. After building, run
`node --test tests/local-handoff-http.test.mjs` for isolated synthetic D1 checks.

## Finding space in the schedule

On phones, the default schedule is a chronological match-card list for one court.
Search stays visible above the collapsed advanced filters, and court buttons switch
the list. Selecting a card opens a native move sheet with date/court filters,
validated available positions and a separate add-to-draft action. Choosing a slot
alone never changes the draft. The sheet supports all available times, manual
editing, player context and swaps. The original timeline and per-match editor
remain optional views; desktop retains the multi-court board and side panel.
The compact draft bar opens the existing reason, conflict review and save flow.

The default court board preserves all occupied positions even when division or
player filters are active; nonmatching matches are muted rather than hidden.
Display rows use at least 15-minute intervals. Placement suggestions retain the
configured scheduling grid, including 5-minute positions between display rows.

Selecting a match computes candidate positions against the current draft, including
rest, daily load, court buffers/availability, dependencies, locks and same-day pool
constraints. Empty positions that fail these checks are not advertised as valid.
Candidate checks are scoped to the selected match and affected relationships; the
server still checks the entire proposed schedule before preview and confirmation.

## Search and blocked-position guidance

Search runs across all dates and courts, using current draft assignments. Exact
match codes (including full-width input) take priority; names and multiple terms
can match players, divisions, dates and courts. Results show their actual date,
time and court. Selecting a result or pressing Enter navigates to the board,
changes date/court, clears a conflicting division filter and focuses the match.
Search/navigation alone never modifies the draft.

Blocked empty positions expose their validation messages inline on mobile and
desktop. When available, the guide offers the next later valid position in the
same court, or another court if none exists. "View" only scrolls/focuses the cell;
placing the match still requires the separate explicit move action. Availability
is recomputed against the current draft; final server validation remains required.

## Exchanges and player context

Select a match, then use **exchange matches** to choose another scheduled match.
The preview swaps start/court simultaneously while retaining each match's duration,
buffer, identities and dependencies. The entire current draft is validated before
the pair can be added. Started/completed matches and assignment locks remain
protected. Cross-day groups, player rest, daily workload and all other existing
constraints still apply. Both edits enter one undo step; final server preview and
confirmation remain required. Nothing is saved just by selecting a candidate.

**Player previous/next matches** uses confirmed participant IDs across divisions
and current draft assignments. It shows adjacent scheduled matches, dates/courts,
estimated rest after play, required rest including transfer, and insufficiency.
The rest calculation is shared with the validator. Unknown future opponents and
unscheduled appearances are not forecast. Clicking a neighboring match uses the
existing board navigation. These are planned intervals, not actual recovery times.

## Rules

The import contract accepts Asia/Taipei events with confirmed-only participants,
at least 60 minutes rest after play, at most 5 matches per player per day, and
08:00–17:30 starts inclusive. Match duration, court buffer, transfer time, per-player
rest, availability/blackouts, dependency/ranking delays, date grid and locks are
preserved from the local workspace. Configured same-day round-robin groups cannot
be split. Unresolved future qualification paths do not reserve player capacity.

Every preview checks the full proposed schedule on the server. Existing conflicts
must also be repaired before confirmation. Started/completed matches cannot be
changed. Configuration, identities and rules cannot be edited through the
API. No overwrite-import or historical rollback is exposed.

Preview IDs are bound to the session and expire after ten minutes. Confirmation
revalidates and compares the original revision atomically. Conflicting concurrent
saves return HTTP 409; stale drafts must be reviewed against the updated version.
Drafts remain in page memory; reloading/closing the page can discard them. Refresh
and logout are disabled while a draft is pending; navigation warns before leaving.

## Storage and authentication

`.openai/hosting.json` declares `DB`. Schema-only migrations are generated from
`db/schema.ts`. Import data is sent through an authenticated private runtime API,
never a migration, client bundle, source commit or deployment artifact.

Configure `APP_ORIGIN` in Sites with the exact HTTPS origin. The ChatGPT sign-in
creates a PK session automatically. Existing administrator records preserve the
primary binding and historical audit names. The application no longer reads
password hashes or requires `ADMIN_PEPPER`.

Session tokens are random 256-bit values stored only as SHA-256 hashes in D1;
HTTPS cookies use HttpOnly, Secure, SameSite=Strict and the __Host- prefix. Sessions
are bound to the platform user and expire after 30 minutes idle or 8 hours total.
Writes require the exact configured Origin and session CSRF token. Logout revokes
the PK session and then signs out of the Site through the platform.

The hosting platform supplies durable D1 storage. A separate disaster-recovery or
backup policy has not been configured by this application.

## Development

Requires Node >=22.13. Follow the Sites skill's installation/build/preview workflow.
For standalone work:

```sh
npm run install:ci
npm run db:generate
npm run build
```

Apply each generated migration once to the local DB using the starter's documented
Wrangler local migration command. Local and production data are separate. Set
`PRIMARY_ADMIN_EMAIL=seedy@sites.test` in ignored `.dev.vars` for an empty local
database, then run `npm run dev` on loopback. Portable preview simulates Sites sign-in; this simulation is absent from
production builds. Never put production credentials in local preview files.

Type check with `npx tsc --noEmit`. Bundle the pure rules/schema into ignored
`outputs/rules.mjs`, `outputs/placements.mjs`, `outputs/navigation.mjs`, `outputs/match-tools.mjs` and `outputs/schema.mjs` using esbuild, then run
`node --test tests/rules.test.mjs tests/placements.test.mjs tests/navigation.test.mjs tests/match-tools.test.mjs`. `tests/http.test.mjs` targets only the loopback
preview at port 8770 and expects an empty disposable local D1 database. It verifies
authorization, import, preview, concurrent saves, history, stale revisions,
logout and session renewal using synthetic data. It must never target production.
After `npm run build`, run `node --test tests/additional-admin-http.test.mjs`
to verify direct sign-in, access revocation and write protection in an isolated
local D1 database.

## Private import

Use the local scheduler's Python environment:

```sh
python scripts/export_schedule.py --scheduler /path/to/squash-scheduler \
  --workspace /path/to/private/workspace --output outputs/private-schedule.json
```

The exporter rejects incompatible rule profiles and unreconciled live-state
workspaces. Upload the resulting file through the private Site's initialization
screen. Keep this file under ignored `outputs/`; never force-add real names or
operational files. A new release does not reinitialize or replace the live DB.

## Public schedule and court control

The owner-private console publishes saved revisions to the existing public reader through a separate HMAC-signed channel. Configure `READER_ORIGIN`, `PUBLISH_SOURCE_ID`, and secret `CLOUD_PUBLISH_SECRET` in Sites runtime settings; the reader must use the same source ID and secret. Keep these values out of source and archives.

Administrators first publish the saved schedule, then confirm each court's actual start or finish. Publication excludes drafts, private player IDs, rules, and audit records. Starting requires two confirmed players and the published assignment; active players cannot appear on two courts. Finishing marks the match completed without fabricating scores. A mistaken start can be cancelled with a reason.

D1 keeps publication state and a durable outbox. Failed delivery remains pending and retries while the console is open or when it reopens. The public reader polls every ten seconds. After its first successful cloud publication, it rejects legacy local-watcher updates. Deployment alone does not publish or start any match.

Portable unit checks require bundling `lib/publication-model.ts` to `outputs/publication-model.mjs` alongside the existing test bundles. `tests/live-http.test.mjs` is an integration check for the synthetic local D1 fixture and the reader preview seeded with the matching three-match snapshot; it must never target production. The original `tests/http.test.mjs` requires a fresh local database.

## Publication comparison

The publish review compares the saved canonical schedule against the last acknowledged public outbox message. Pending, undelivered messages do not become the comparison baseline. The review shows match identity, additions/removals, and before/after time and court. First publication explicitly has no cloud baseline.

The confirmation captures its revision and publication sequence. Polling never replaces the reviewed content silently; subsequent edits or court updates disable confirmation until the administrator reviews again. The existing server-side revision/sequence checks also reject stale submissions. Drafts remain excluded. Bundle `lib/publication-diff.ts` to `outputs/publication-diff.mjs` to run `tests/publication-diff.test.mjs`.

## Court scorekeeping

Choose a confirmed, scheduled match from any idle court and verify the court and
both players. Before the first cloud publication, matches remain selectable from
the saved schedule. The start confirmation guides the operator through reviewing
and publishing the schedule, preserves the chosen match, and returns to the start
confirmation. Scoring opens only after the separate explicit start confirmation. Changing the court checks assignment locks and schedule
conflicts in both the canonical and published schedules. Other unpublished edits
are not included. The scoring dialog opens automatically and can be resumed from
the active court card. It does not start matches based on the clock.

Each tap updates the displayed score immediately and is then saved to D1 before
the next operation. The display is provisional while saving; failed requests are
reconciled against the server without replaying the tap. If the latest score cannot
be read, further scoring and completion stay disabled until recovery. Undo removes the last point,
including reopening a completed game. Games advance automatically; a match stops
when one side wins the required number of games. The existing tournament uses
best of three, 11 points, win by two, no cap. New imports carry supported stage
rules (best of three/five, 11/15 points, win by two, no cap). Reloading the page
retains the point history; reopen scoring from the active court. Scoring requires
an online authenticated session and does not queue offline taps.

Score versions reject stale concurrent writes. Final confirmation reads the
persisted score, validates victory, and atomically checks both the schedule and
score versions before marking the match completed, freeing the court, and queuing
the result. Synchronization failures retain the result and show a retry status.
The public Site displays the winner and each game score after its normal refresh.
In-progress game scores, game wins and completed games synchronize through the
small public live-score channel; the raw tap history remains private.

This version supports normal completed matches. Walkovers, retirements, corrections
of confirmed results, automatic pool rankings and advancement require a subsequent
workflow. Public standings affected by new results explicitly await adjudication;
stale points/ranks are hidden rather than presented as current.

## Live score synchronization

The score API acknowledges each point or undo as soon as it is committed to D1.
Cloudflare `waitUntil` sends the allowlisted summary in the background to
`/sync/cloud-score` using the existing runtime HMAC secret and a path-bound,
two-minute signature. Score versions and actual start timestamps prevent stale
writes and scores from an earlier start from appearing in a new match. D1 stores
the last acknowledged score version and a retry status. The management console
retries pending summaries while open, including after leaving the scoring dialog.
Failures never undo or silently discard saved points.

The participant reader polls the small public score document about every two
seconds while visible, with reconnect/visibility refresh. The channel also carries
the current schedule version so starts and finishes trigger a schedule refresh.
It displays in-progress scores, game wins, game history and explicit pending
victory confirmation. Confirmed results still use the existing publication outbox.
No full schedule snapshot is rewritten for individual points.

The scoring dialog loads the saved score before attempting any publisher retry.
It polls saved state every two seconds, skips polls during writes and discards
responses that began before a write. A delayed poll cannot overwrite the immediate
score preview. Publication timeouts never block the next saved point; pending
summaries remain retryable through the existing D1 acknowledgement metadata.

## Separate operations pages

`/` and `/live` open court selection and scoring. `/schedule` contains the court
availability board, draft editing, history and publication review. Both direct
routes retain platform and administrator authentication. Navigating from an
unsaved schedule draft to scoring is blocked until it is saved or discarded.

Search for a published match by number or player, then select an available
court at start. The actual court appears on the live scoreboard without
changing the scheduled assignment. Publish schedule edits before starting an
affected match. The live mutation also
rejects cross-court starts: use schedule editing and publication to move a match.

Completed board/list matches use blue with a completion label; in-progress board
matches use amber. Filtered-out matches retain these status colors. Both remain
non-editable, and completed matches continue occupying their planned intervals.

## Date-specific queues and unattended delivery

The court page defaults to the current Asia/Taipei event day. Outside event dates,
it chooses the next event date or the latest past event date and explains the
fallback. Each court shows only that date's matches and highlights its earliest
eligible match. Selecting that suggestion still requires the ordinary start
confirmation. Switching dates clears selections and open start confirmations;
active matches remain visible regardless of the date filter.

`POST /api/sync-service` is a narrow unattended updater. It accepts no mutation
payload, browser session or CSRF token. Authorization requires the existing Sites
service credential, matched against its SHA-256 fingerprint in the secret runtime
variable `SYNC_RETRY_TOKEN_HASH`. The raw credential stays outside this repository.
Calls resend the existing durable publication first, then active match score
summaries. Responses contain counts, publication sequence/version and a timestamp,
with no player names. Unknown credentials and request bodies are rejected.

The linked Sites cloud task retrieves service access afresh with `get_site` and
sends that credential only to this Site, in `OAI-Sites-Authorization` and
`Authorization`. It uses the supported cloud scheduler rather than a local cron
or an open browser. A rotated service credential requires updating the configured
fingerprint. The existing immediate delivery and foreground retry remain enabled.

Bundle `lib/court-queue.ts` and `lib/sync-service-auth.ts` to the matching ignored
`outputs/*.mjs` for their unit tests. `tests/sync-service-http.test.mjs` uses only
the disposable local admin at 8770 and a fake reader at 8780. It verifies reader
failure, logout, sessionless recovery, idempotency and unchanged canonical data.
Never run this integration test against production.

## Mobile move verification

Phone cards preserve completed/in-progress colors and keep locked matches read-only.
The move sheet reuses `placementOptions` against the complete current draft, so
filters never remove hidden court occupancy, rest or group constraints. Targets
are rechecked as the draft changes and require explicit confirmation before they
enter the draft. Saving still uses the existing full server review. Search results
open the matched card's move sheet across dates/courts. Viewing or canceling the
sheet makes no persistent change.

## Source-only checkout

No deployment identity or runtime secrets are included. Before local builds, create an ignored `.openai/hosting.json` with your own Sites project ID and bindings `"d1": "DB", "r2": null`. Runtime settings belong in Sites or ignored local `.dev.vars`; never commit real schedules or production credentials.

## Switch to the 226-match draft

The primary administrator can replace the 279-match schedule with the approved
226-match draft. Set `DRAFT_CUTOVER_SOURCE_REVISION` to the local export's
`source_revision` and `DRAFT_CUTOVER_SHA256` to the SHA-256 digest of that exact
JSON file. The switch stays disabled without both settings.

Open the schedule page, select the approved JSON, and confirm the switch. The
operation archives the prior schedule, scores, and publication state in D1.
Publish the replacement schedule after the reader is serving the same match
IDs and codes. Remove the cutover settings when no longer needed.

## Manual scoring on an older iPad

Open `/ipad` in Safari and sign in as a tournament administrator. Select the
match by number or player, then select an available court to start. Use the two large buttons to save points, and use **復原上一分**
to undo a mistake. The page refreshes only when the operator asks for data or
performs an action. Publish the initial schedule in the full console before
starting matches. The manual page uses the same live and score APIs, and the
server still synchronizes saved points to the reader.

Use **直接登錄完賽** to enter a full result from paper scores. Enter both
players' points for each played game and leave unplayed games blank. You can
record a published match without first marking it in progress, or replace
an active match's point-by-point tally with the confirmed game scores.

## Password administrator access

Set ADMIN_AUTH_MODE=password and TEMP_ADMIN_PASSWORD_HASH in the Sites runtime settings to pause ChatGPT identity
and use /temporary-login. The hash format is
pbkdf2_sha256$100000$<16-byte salt in hex>$<32-byte SHA-256 digest in hex>.
Store the hash as a secret. TEMP_ADMIN_EXPIRES_AT is optional; omit it for ongoing password access. If set, it must be a valid future ISO UTC timestamp.

Password sessions have full administrator privileges, including rules, results, publication, backup and tournament handoff. Existing administrator records stay unchanged.
Sessions expire after 30 idle minutes or eight hours, and the configured expiry
also invalidates active sessions. Login attempts share a five-per-minute limit.
Change the hash to revoke all temporary sessions. Set ADMIN_AUTH_MODE=chatgpt
and redeploy to restore ChatGPT login; remove the temporary hash and expiry.
