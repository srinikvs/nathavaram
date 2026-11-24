# Sync protocol

Phase 2 is an offline Android app using the same voter fields. It can create and edit rows without a network, then exchange them with this server. Phase 1 implements the server side and the tests. It does not build the APK.

Authentication is the same JWT as the rest of the API. Send `Authorization: Bearer <token>`. Users and passwords are not synced. Each device signs in as a person, then syncs voters.

All timestamps are UTC in the form `YYYY-MM-DDTHH:MM:SS.ffffffZ`. A value without a timezone is read as UTC. `server_time` on every sync response lets the device measure clock skew. Last-write-wins is unsafe when a phone clock is wrong, so the device should warn the operator when it differs from `server_time` by more than a few minutes.

## Identifiers

- **`voter_id` (the EPIC-style number) is the primary key.** There is no separate `id`. The device keys its local rows, its pushes and the `voter_id` in every result by it. It is stored upper-case; the server upper-cases what it receives, and a value that is not 3–32 letters or digits is a `validation_error` for that change.
- A voter cannot be renamed: a different `voter_id` is a different record. To "change" a VoterId, delete the old record and create a new one.
- Because the key is the voter itself, two devices that create the same `voter_id` offline are not a clash: their changes merge by last-write-wins on `updated_at` (see the conflict table).
- The booth and ward fields are named `booth` and `ward` on the wire (they were `booth_2024` and `ward_2026`). Push still accepts the old keys; pull and `server_record` return only the new ones.
- `revision` is assigned only by the server. Clients treat it as opaque and store the newest value they have seen.
- `source_id` on an applied push is the `device_id` from the batch.

## Pull

`GET /api/sync/pull?since=<revision>&limit=<1-1000>`

Returns every voter, including soft-deleted rows, whose `revision` is greater than `since`, ordered by `revision` ascending. `limit` defaults to 500. The personal fields returned for a soft-deleted row depend on the caller's role (see below); a `user` never receives the personal fields of a soft-deleted voter.

```json
{
  "server_time": "2026-11-11T21:00:00.000000Z",
  "server_revision": 42,
  "since": 10,
  "has_more": false,
  "changes": [
    {
      "voter_id": "ZZZ0000001",
      "first_name": "Testperson",
      "updated_at": "2026-11-11T18:04:00.000000Z",
      "revision": 11,
      "deleted_at": null
    }
  ]
}
```

Two kinds of deleted row come back on pull:

- **Soft delete** (`deleted_at` set, `hard_deleted_at` null): for a manager or admin, every field is kept, exactly as it was when the voter was removed. For a `user`, who cannot review deleted voters, the row is a **stripped tombstone** on pull and in every `server_record`: `voter_id`, `revision`, `created_at`, `updated_at`, `deleted_at`, `source_id` and `hard_deleted_at` are sent, `first_name` is `""`, `is_local` is `false`, and every other personal field is null. The device should hide or remove its active row. A manager or admin can restore the voter, and the restore arrives as an ordinary active row with a new `revision`.
- **Hard delete** (`hard_deleted_at` set): the personal data is gone. Every voter field is null, `first_name` is `""` and `is_local` is `false`. Only `voter_id`, `revision`, `created_at`, `updated_at`, `deleted_at`, `hard_deleted_at` and `source_id` are meaningful. The device should delete its local copy. A hard-deleted row cannot be restored; a later non-delete change for the same `voter_id` is a new record. Tombstones that were written before the voter_id-key change hold an inert placeholder `voter_id` (the old row id without dashes) because their real VoterId had already been erased; a device will not find a local row for those. When `has_more` is true, call again with `since` set to the `revision` of the last change in this page. Stop when `has_more` is false. An empty `changes` list with `server_revision` equal to the device cursor means the device is caught up.

## Push

`POST /api/sync/push`

Send the latest snapshot of each changed row, not the intermediate history. A batch may contain up to 500 changes. The HTTP status is 200 when the batch was processed. Each item has its own status, so one bad row does not reject the rest. The server commits the applied rows together.

A non-delete is a full snapshot. Omitted optional fields are stored as null. `voter_id` and `first_name` are required. A delete sets `deleted` to true and needs `voter_id` and `updated_at`.

```json
{
  "device_id": "android-6f1c",
  "changes": [
    {
      "updated_at": "2026-11-11T18:04:00.000000Z",
      "created_at": "2026-11-11T16:00:00.000000Z",
      "deleted": false,
      "base_revision": 10,
      "voter_id": "ZZZ0000001",
      "first_name": "Testperson",
      "last_name": "One",
      "house_no": "12-A",
      "street": "Main St",
      "is_local": true,
      "age": 42,
      "party": null
    }
  ]
}
```

`base_revision` is the revision the device last saw for this voter. When it is present and the server row's `revision` is greater, the server has changed since the device last synced, so the change is a `conflict` even if the device `updated_at` is newer. Nothing is written and the server row is returned. A device that omits `base_revision` gets plain last-write-wins by `updated_at`. An exact replay of an already-stored change is still `applied` ("Already stored"), whatever `base_revision` says.

Each change in a batch is applied in its own savepoint. A rejected change (conflict, validation error, or a database key race) never rolls back or blocks the other changes in the batch.

```json
{
  "server_time": "2026-11-11T21:00:01.000000Z",
  "server_revision": 43,
  "results": [
    {
      "voter_id": "ZZZ0000001",
      "status": "applied",
      "server_revision": 43,
      "server_record": {}
    }
  ]
}
```

`server_record` is present for `applied`, `conflict` and `forbidden` when the server has a row to show. The device should replace its local copy with `server_record` whenever it is present.

## Conflict policy

Last-write-wins by `updated_at`, then a new server revision.

| Situation | Status | What the server does |
| --- | --- | --- |
| No row with this `voter_id`, and the change is not a delete | `applied` | Inserts the snapshot. `created_at` is the device value when it is valid, otherwise `updated_at` |
| No row with this `voter_id`, and the change is a delete | `applied` | No tombstone is written. The message is `No server record; delete ignored`. Send the latest state only; a create that never reached the server does not need a tombstone |
| Incoming `updated_at` is newer than the stored row (and `base_revision` is absent or current) | `applied` | Replaces the data columns, sets `source_id` to `device_id`, and assigns the next revision. A newer delete is a **soft delete**: it sets `deleted_at` and keeps every field (see Pull). A newer non-delete onto a soft-deleted row is a **restore** and needs a manager or admin (see the next rows). A newer non-delete onto a hard-deleted row (`hard_deleted_at` set) creates a new record under the same `voter_id` |
| Incoming `updated_at` is older | `conflict` | Keeps the server row and returns it |
| `base_revision` is lower than the server row's `revision` (and the change is not an exact replay) | `conflict` | Keeps the server row and returns it, for edits and for deletes. The device should show the difference and let the operator re-apply |
| Two changes insert the same new `voter_id` at the same instant (a database key race) | `conflict` | Only the loser is rejected, and `server_record` is the stored row; the rest of the batch is stored |
| Equal `updated_at` and the same content, including the deleted flag | `applied` | Idempotent replay. The revision does not move |
| Equal `updated_at` and different content | `conflict` | The server row wins, so two devices that stamp the same instant cannot flap |
| `voter_id` is empty or not 3–32 letters or digits | `validation_error` | Nothing is written for that item. `voter_id` in the result is the value as received |
| `updated_at` before 2000-01-01 UTC, more than 5 minutes ahead of the server, or otherwise invalid | `validation_error` | Nothing is written for that item |
| The signed-in user has the `user` or `manager` role and the change would alter a field outside `USER_EDITABLE_FIELDS` (Booth, Ward, Booth S.No, Ward S.No, RelativeName, HouseNo, Age, Street, Mobile, Village, Panchayath, Current City, Local/Non-Local, Party) of an existing active row | `forbidden` | Nothing is written. `message` is `Your role cannot change: <labels>. Users and managers may edit only <the editable labels>; ask an admin to change other fields.` and `server_record` is the current server row |
| The signed-in user has the `user` role and the change is a non-delete onto a soft-deleted row (a restore) | `forbidden` | Nothing is written. `message` is `Your role cannot restore deleted voters; ask a manager or admin to restore this voter.` Only manager and admin may restore |
| A delete of an active row, from any role | `applied` | Soft delete: `deleted_at` is set and every field is kept. Hard delete is not available through sync; it is a web or REST action for manager and admin |
| A delete of a row that is already hard-deleted | `applied` | Nothing changes. `message` is `Already permanently deleted` |

The old `voter_id_conflict` status no longer exists: the same `voter_id` is the same record, so there is nothing to clash with.
`voter_id` uniqueness ignores soft-deleted rows. After a delete is synced, another row may use that EPIC.

### Roles and sync

`forbidden` is the sync form of HTTP 403: it is reported per change, with HTTP 200 for the batch, like `validation_error` and `conflict`, so one refused change never blocks the others. The `server_record` in every result (`applied`, `conflict`, `forbidden`) is the same view as pull: a `user` who cannot review deleted voters gets the stripped tombstone for a soft-deleted row, never its personal fields. The comparison is against the stored values, so a full snapshot whose restricted fields are unchanged is **not** a violation; only a real difference is. Stale and replayed changes are decided first (`conflict`, "Already stored"), so a device that is merely behind is told `conflict`, not `forbidden`. Every role may create a new voter (a change for a `voter_id` the server has no active row for) and may set every column on it. `manager` has the same field rules as `user`, plus restore. `admin` accounts, including the owner, may change every column except `voter_id`.

### Bulk edits and deletes

Bulk edit (web and `POST /api/voters/bulk-edit`) and Master Data rename (see `SCHEMA.md`) write each affected voter individually: every row gets its own new `revision`, `updated_at` and `source_id` (`web:<username>`). A bulk delete of active voters is a soft delete, the same as a single delete: every field is kept and the row is pulled with `deleted_at` set. Bulk hard delete is web and REST only (manager and admin); devices see its result as a hard-deleted row on pull. Devices need no new handling; the changes arrive through normal pull. These writes are all-or-nothing on the server, so a pull never sees half of a bulk operation committed.

### Fields in a snapshot

A snapshot carries the columns of the voter list: `booth`, `booth_s_no`, `ward`, `ward_s_no`, `first_name`, `last_name`, `relative_name`, `house_no`, `gender`, `age`, `street`, `mobile`, `caste`, `village`, `panchayath`, `mandal`, `current_city`, `is_local`, `party` and `constituency`. `is_local` is a boolean (the strings `Local` and `Non-Local` are also accepted; the list, form, CSV and Excel show Local / Non-Local). Omitted `is_local` is stored as false. `gender` is optional (null, `Male`, `Female` or `Other`; other case variants are canonicalized and anything else is rejected with 422). A client that does not know the field may omit it, but a push that omits it is read as null: for the `user` role that counts as a change to Gender on a voter that has one and is answered `forbidden`, so update Android clients to echo `gender` back. `permanent_address`, `profession`, `surname`, `address`, `c_city`, `bs_no_2024` and `id` no longer exist: a push that still sends them is rejected as an unknown field (HTTP 422 for the whole batch), so update the client together with the server. Hard-deleted rows carry `is_local: false`, `first_name` `""` and null text.

The server clock wins for edits made through the website and the REST CRUD API: those set `updated_at` to the current server time and therefore beat an offline edit that was stamped earlier. An offline edit stamped later than the server edit wins on the next push. That is ordinary last-write-wins. It is not a field-level merge. If two people edit different columns of the same voter, the older edit disappears.

## What the Android client should do

1. Sign in and store the JWT.
2. Keep a cursor, initially 0.
3. On connectivity: push local rows whose local dirty flag is set, using each row's `voter_id` and its last local `updated_at`.
4. For every result with `server_record`, replace the local row and clear the dirty flag. For `conflict`, keep the server row and show the operator the difference.
5. Pull with `since` = cursor until `has_more` is false. Apply changes, including tombstones, unless the local row is still dirty and its `updated_at` is newer than the pulled one (it still needs to be pushed).
6. Store `server_revision` as the cursor only after the page is applied. When a page is non-empty, the next `since` can be the last change's `revision`.
7. Use SQLite/Room with the same snake_case columns, `voter_id` primary key, `updated_at`, `revision`, `deleted_at`, and `source_id`.

Phase 2 still has to build the Room database, the background sync worker, the offline UI, and the APK. The HTTP contract above is the part this server already accepts.

## Android client

The client lives in [`android/`](../android/README.md) and follows this contract: its Room database (version 3) has `voter_id` as the primary key, it pushes and matches results by `voter_id`, and it has no `id`, `surname`, `address`, `gender`, `permanent_address`, `profession` or `c_city`. Migration 2 → 3 (see the Android README) carries pending edits across, so nothing queued on a phone is lost, but the three dropped columns' values are. An app build from before this change cannot sync with this server (HTTP 422), and this app build needs a server that has migration `0007`.

For a `user` or `manager` the edit screen shows every field outside `USER_EDITABLE_FIELDS` read-only (FirstName, LastName, Gender, Mandal, Constituency and Caste), and the repository takes those fields from the stored row on save, so a restricted change is never queued for push. Soft delete is allowed for every role; restore and hard delete are not. **Protocol change for the Android follow-up:** the editable set changed (Caste is now admin-only; Booth, Ward, Booth S.No, Ward S.No, RelativeName, Age, Village, Panchayath, Current City and Local/Non-Local are now user-editable), a soft delete keeps its fields (the device should hide the row, not expect an empty tombstone), and pull carries `hard_deleted_at`. This server change does not touch `android/`. VoterId is read-only for everyone once the voter exists. A `forbidden` result is handled like `conflict`: the server row replaces the local one and the operator sees the reason. Adding a VoterId that exists locally only as a tombstone revives it and pushes with the tombstone's revision as `base_revision`. When a pulled change is skipped because a dirty local row is newer, the client still raises that row's stored `revision` to the pulled one, so its next push carries a current `base_revision` and wins by `updated_at` instead of being rejected as a stale `conflict`.
