# PK schedule administration

Private Sites application for adjusting an existing squash tournament schedule.
The Python solver and PDF exports remain local. This application never calls them.

## Supported flow

1. Open the owner-private Site through the Sites platform sign-in.
2. Import an exported schedule once and choose an administrator username/password.
3. Select a match in the court board, then click a green available position or a
   nearby suggestion. Repeat for other matches; undo supports the last 20 draft
   changes. The list/manual editor remains available.
4. Enter a reason and review all conflicts and before/after changes.
5. Confirm the immutable preview. A D1 transaction saves a new revision and history.

The first administrator is bound to the platform identity that initializes the
private Site. Expanding Site access does not grant another visitor administrator
rights. The existing platform authentication integration is preserved. There is
one administrator, with no signup, account sharing, password reset or account
recovery screen in this version. Keep a unique password in your password manager.

Saved cloud schedule edits must be explicitly published to the spectator Site.
Court starts and finishes synchronize automatically after the first publication.
The original local workspace and existing PDFs are separate fixed versions.
Automatic re-planning and results entry are outside this version.

## Finding space in the schedule

On phones, the board shows one court at a time with court tabs. Match selection
stays above the timeline; available-position suggestions open in a native modal
bottom sheet. Choosing a different court's suggestion switches the visible court.
Search/filters are collapsible, and the compact draft bar opens the reason/review
sheet on demand. Desktop keeps the multi-court board and side panel.

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
changed. Configuration, identities, results and rules cannot be edited through the
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

Production secrets are configured in Sites: `ADMIN_PEPPER` (secret) and
`APP_ORIGIN` (the exact HTTPS origin). The pepper is HMAC-applied before PBKDF2
SHA-512 with a random salt and 100,000 iterations, within Workerd's native limit.
The salt/hash live in D1; plaintext passwords are never stored or logged. Preserve
the pepper across releases; replacing it invalidates existing password hashes.
Never export it into Git or client code.

Session tokens are random 256-bit values stored only as SHA-256 hashes in D1;
HTTPS cookies use HttpOnly, Secure, SameSite=Strict and the __Host- prefix. Sessions
are bound to the platform user and expire after 30 minutes idle or 8 hours total.
Writes require the exact configured Origin and session CSRF token. Logout revokes
the session. Login is limited to five attempts per minute per platform user in D1.

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
Wrangler local migration command. Local and production data are separate. Configure
a local-only random `ADMIN_PEPPER` in ignored `.dev.vars`, then `npm run dev` on
loopback. Portable preview simulates Sites sign-in; this simulation is absent from
production builds. Never put production credentials in local preview files.

Type check with `npx tsc --noEmit`. Bundle the pure rules/schema into ignored
`outputs/rules.mjs`, `outputs/placements.mjs`, `outputs/navigation.mjs`, `outputs/match-tools.mjs` and `outputs/schema.mjs` using esbuild, then run
`node --test tests/rules.test.mjs tests/placements.test.mjs tests/navigation.test.mjs tests/match-tools.test.mjs`. `tests/http.test.mjs` targets only the loopback
preview at port 8770 and expects an empty disposable local D1 database. It verifies
setup, authorization, import, preview, concurrent saves, history, stale revisions,
logout and login using explicitly synthetic data. It must never target production.

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

## Source-only checkout

No deployment identity or runtime secrets are included. Before local builds, create an ignored `.openai/hosting.json` with your own Sites project ID and bindings `"d1": "DB", "r2": null`. Runtime settings belong in Sites or ignored local `.dev.vars`; never commit real schedules or production credentials.
