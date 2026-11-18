# Nathavaram Android client

Offline-first Kotlin/Compose app for the Nathavaram voter list. It keeps a local Room copy, lets an operator search and edit one record at a time without a network, and syncs with the server through `GET /api/sync/pull` and `POST /api/sync/push` exactly as described in [`docs/SYNC.md`](../docs/SYNC.md). Fields come from [`docs/SCHEMA.md`](../docs/SCHEMA.md).

This is an internal build. It is not prepared for any store listing.

<!-- Login rate limiting is queued and must land before any test deploy. -->

## Scope

- Regular-user flow only: sign in, search, view/edit one record, add a record, review sync conflicts.
- No bulk export, import or mass delete, in the UI or in the API calls. No user management.
- The only endpoints used are `POST /api/auth/login`, `GET /api/sync/pull` and `POST /api/sync/push`.

## Build and test

Requires JDK 17 and an Android SDK (`ANDROID_HOME` or an untracked `local.properties`). Gradle 8.9 and AGP 8.5.2.

`gradle/wrapper/gradle-wrapper.jar` is not committed. Download it first (for example `gradle wrapper --gradle-version 8.9` with a local Gradle), then:

```bash
cd android
./gradlew testDebugUnitTest     # JVM tests only, no emulator
./gradlew assembleDebug
```

Robolectric downloads its Android jar on the first test run, so that run needs network access.

## Server address

A fresh install uses the built-in default `https://nathavaram.duckdns.org` (no trailing slash), so first run goes straight to sign-in. The address can still be changed on the **Server address** screen. A 401 from a revoked or expired token clears the stored token and returns to sign-in with "Sign in again". Release builds accept `https://` only and the network security config disables cleartext. The debug build alone allows `http://` (`src/debug/res/xml/network_security_config.xml`) so a developer can reach a local server, for example `http://10.0.2.2:8000` from the emulator.

## How it works

| Piece | File |
| --- | --- |
| Room tables (database version 3): `voters` (all SCHEMA columns plus local `dirty`, **`voter_id` is the primary key**), `sync_state` (pull cursor `server_revision`), `conflicts` (keyed on `voter_id`) | `store/Entities.kt`, `store/Daos.kt`, `store/Migrations.kt` |
| Save: validates, uppercases VoterId, canonical Local/Non-Local and party, stamps `updated_at`, sets `dirty`, queues a sync. Adding a VoterId that is only a soft-deleted row revives that row | `store/VoterRepository.kt` |
| Merge and conflict rules (pure) | `sync/SyncMerge.kt` |
| Push then pull loop, per-item results | `sync/SyncEngine.kt` |
| WorkManager (needs network, exponential backoff, 15 min periodic) | `sync/SyncScheduler.kt`, `sync/SyncWorker.kt` |
| HTTP, `Authorization: Bearer` only (no cookies, no CSRF header) | `net/HttpApi.kt` |
| JWT in EncryptedSharedPreferences | `security/TokenStore.kt` |

Sync behavior:

- Dirty rows are pushed as full snapshots with `base_revision` set to the last revision seen for that voter (omitted for a row never synced). The key on the wire is `voter_id`; there is no row id.
- Every result is applied on its own, so one `conflict` never drops the rest of the batch.
- `applied`: the row is replaced by `server_record` and the dirty flag is cleared. If the operator edited the row again while the push was in flight, the newer edit stays dirty.
- `conflict` / `forbidden`: the server row replaces the local one and an entry appears under **Review** with the differing fields. (`voter_id_conflict` no longer exists: the same `voter_id` is the same record, so two devices that add it offline merge by last-write-wins.)
- `validation_error`: the row stays dirty, with the server message under **Review**. Saving the record again clears the note.
- Pull runs until `has_more` is false. Tombstones are stored stripped (only the `voter_id` key is kept) and hidden from search; a newer record for the same `voter_id` revives it. A dirty local row newer than the pulled one is not overwritten; its revision moves up to the one just seen so its next push is not rejected as stale.
- The cursor is stored only after a page is applied.
- If the phone clock differs from `server_time` by more than 5 minutes, the search screen shows a warning.
- HTTP 403 with code `password_change_required` (at `detail.code` or top-level `code`) is a forced password change, not a generic error. Login or sync stops (no retry), sign-in and the search screen show a message to change the password on the web change-password page and sign in again, and the flag clears on the next successful sync or sign-in. There is no in-app change-password screen. Any other 403 stays a plain HTTP error. Parsing is in `net/PasswordChangeRequired.kt`, text in `ui/ErrorMessages.kt`.

## Upgrading the local database (v2 → v3)

`Migrations.MIGRATION_2_3` rebuilds the `voters` table so `voter_id` is the primary key, mirroring server migration `0007`: `bs_no_2024`, `surname`, `c_city` become `booth_s_no`, `last_name`, `current_city`; `address` becomes `street` with a leading `H.No <n>,` split into `house_no`; `panchayath` and `mandal` are added; `gender`, `permanent_address` and `profession` are **dropped** (their values are not kept). Dirty rows keep their flag and revision, so pending edits still sync. If two rows ever shared a `voter_id` the newest `updated_at` is kept. The `conflicts` review notes are cleared because they were stored in the old shape. This app version needs the server version that has migration `0007`; an older app cannot sync with it.

## Privacy

No `Log.*` calls are used anywhere. The token, password and voter fields are never logged, and API errors carry fixed messages, never response bodies. `allowBackup` is off. Tests use synthetic data only (fake names, `90000000xx` mobiles, `ZZZ00000xx` voter ids).

## Not done

- Release signing and store publishing, by design.
- No in-app delete, since delete is admin-only on the server.
- No instrumented (emulator) tests, and no WorkManager worker test.
