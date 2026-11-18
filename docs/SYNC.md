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

Returns every voter, including soft-deleted rows, whose `revision` is greater than `since`, ordered by `revision` ascending. `limit` defaults to 500.

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

`deleted_at` set means the row was removed. The device should delete or tombstone its local copy, not resurrect it. A tombstone carries no personal data except its key: every voter field is null and `first_name` is `""`. Only `voter_id`, `revision`, `created_at`, `updated_at`, `deleted_at`, and `source_id` are meaningful. Tombstones that were written before the voter_id-key change hold an inert placeholder `voter_id` (the old row id without dashes) because their real VoterId had already been erased; a device will not find a local row for those. When `has_more` is true, call again with `since` set to the `revision` of the last change in this page. Stop when `has_more` is false. An empty `changes` list with `server_revision` equal to the device cursor means the device is caught up.

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
| Incoming `updated_at` is newer than the stored row (and `base_revision` is absent or current) | `applied` | Replaces the data columns, sets `source_id` to `device_id`, and assigns the next revision. A newer non-delete clears `deleted_at` (this **revives** a tombstone under the same `voter_id`). A newer delete sets `deleted_at` and strips every personal column (see Pull) |
| Incoming `updated_at` is older | `conflict` | Keeps the server row and returns it |
| `base_revision` is lower than the server row's `revision` (and the change is not an exact replay) | `conflict` | Keeps the server row and returns it, for edits and for deletes. The device should show the difference and let the operator re-apply |
| Two changes insert the same new `voter_id` at the same instant (a database key race) | `conflict` | Only the loser is rejected, and `server_record` is the stored row; the rest of the batch is stored |
| Equal `updated_at` and the same content, including the deleted flag | `applied` | Idempotent replay. The revision does not move |
| Equal `updated_at` and different content | `conflict` | The server row wins, so two devices that stamp the same instant cannot flap |
| `voter_id` is empty or not 3–32 letters or digits | `validation_error` | Nothing is written for that item. `voter_id` in the result is the value as received |
| `updated_at` before 2000-01-01 UTC, more than 5 minutes ahead of the server, or otherwise invalid | `validation_error` | Nothing is written for that item |
| The signed-in user has the `user` role and the change would alter a field other than Party, Caste, Mobile, HouseNo, Street of an existing active row | `forbidden` | Nothing is written. `message` is `Your role cannot change: <labels>. Regular users may edit only Party, Caste, Mobile, HouseNo, Street; ask an owner or admin to change other fields.` and `server_record` is the current server row |
| The signed-in user has the `user` role and the change is a delete of an active row | `forbidden` | Nothing is written. Deleting is admin-only on every path, so it cannot be used to delete and re-add a voter |

The old `voter_id_conflict` status no longer exists: the same `voter_id` is the same record, so there is nothing to clash with.
`voter_id` uniqueness ignores soft-deleted rows. After a delete is synced, another row may use that EPIC.

### Roles and sync

`forbidden` is the sync form of HTTP 403: it is reported per change, with HTTP 200 for the batch, like `validation_error` and `conflict`, so one refused change never blocks the others. The comparison is against the stored values, so a full snapshot whose restricted fields are unchanged is **not** a violation; only a real difference is. Stale and replayed changes are decided first (`conflict`, "Already stored"), so a device that is merely behind is told `conflict`, not `forbidden`. A `user` may still create a new voter (a change for a `voter_id` the server has no active row for) and may set every column on it. `admin` accounts, including the owner, may change every column.

### Fields in a snapshot

A snapshot carries the columns of the voter list: `booth`, `booth_s_no`, `ward`, `ward_s_no`, `first_name`, `last_name`, `relative_name`, `house_no`, `age`, `street`, `mobile`, `caste`, `village`, `panchayath`, `mandal`, `current_city`, `is_local` and `party`. `is_local` is a boolean (the strings `Local` and `Non-Local` are also accepted; the list, form, CSV and Excel show Local / Non-Local). Omitted `is_local` is stored as false. `gender`, `permanent_address`, `profession`, `surname`, `address`, `c_city`, `bs_no_2024` and `id` no longer exist: a push that still sends them is rejected as an unknown field (HTTP 422 for the whole batch), so update the client together with the server. Tombstones carry `is_local: false` and null text.

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

For a `user` the edit screen shows every field except Party, Caste, Mobile, HouseNo and Street read-only, and the repository takes the other fields from the stored row on save, so a restricted change is never queued for push. VoterId is read-only for everyone once the voter exists. A `forbidden` result is handled like `conflict`: the server row replaces the local one and the operator sees the reason. Adding a VoterId that exists locally only as a tombstone revives it and pushes with the tombstone's revision as `base_revision`. When a pulled change is skipped because a dirty local row is newer, the client still raises that row's stored `revision` to the pulled one, so its next push carries a current `base_revision` and wins by `updated_at` instead of being rejected as a stale `conflict`.
