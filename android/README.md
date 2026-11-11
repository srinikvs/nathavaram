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

There is no built-in address. The first run opens the **Server address** screen. Release builds accept `https://` only and the network security config disables cleartext. The debug build alone allows `http://` (`src/debug/res/xml/network_security_config.xml`) so a developer can reach a local server, for example `http://10.0.2.2:8000` from the emulator.

## How it works

| Piece | File |
| --- | --- |
| Room tables: `voters` (all SCHEMA columns plus local `dirty`), `sync_state` (pull cursor `server_revision`), `sync_conflicts` | `data/Entities.kt`, `data/Daos.kt` |
| Save: validates, uppercases VoterId, canonical gender/party, client UUID, stamps `updated_at`, sets `dirty`, queues a sync | `data/VoterRepository.kt` |
| Merge and conflict rules (pure) | `sync/SyncMerge.kt` |
| Push then pull loop, per-item results | `sync/SyncEngine.kt` |
| WorkManager (needs network, exponential backoff, 15 min periodic) | `sync/SyncScheduler.kt`, `sync/SyncWorker.kt` |
| HTTP, `Authorization: Bearer` only (no cookies, no CSRF header) | `net/HttpApi.kt` |
| JWT in EncryptedSharedPreferences | `security/TokenStore.kt` |

Sync behavior:

- Dirty rows are pushed as full snapshots with `base_revision` set to the last revision seen for that id (omitted for a row never synced).
- Every result is applied on its own, so one `conflict` never drops the rest of the batch.
- `applied`: the row is replaced by `server_record` and the dirty flag is cleared. If the operator edited the row again while the push was in flight, the newer edit stays dirty.
- `conflict` / `voter_id_conflict`: the server row replaces the local one and an entry appears under **Review** with the differing fields. For `voter_id_conflict` on a row the server never saw, the local row is dropped and the existing voter is kept.
- `validation_error`: the row stays dirty, with the server message under **Review**. Saving the record again clears the note.
- Pull runs until `has_more` is false. Tombstones are stored stripped (`voter_id` is the inert placeholder, never read as an EPIC) and hidden from search. A dirty local row newer than the pulled one is not overwritten; its revision moves up to the one just seen so its next push is not rejected as stale.
- The cursor is stored only after a page is applied.
- If the phone clock differs from `server_time` by more than 5 minutes, the search screen shows a warning.

## Privacy

No `Log.*` calls are used anywhere. The token, password and voter fields are never logged, and API errors carry fixed messages, never response bodies. `allowBackup` is off. Tests use synthetic data only (fake names, `90000000xx` mobiles, `ZZZ00000xx` voter ids).

## Not done

- Release signing and store publishing, by design.
- No in-app delete, since delete is admin-only on the server.
- No instrumented (emulator) tests, and no WorkManager worker test.
