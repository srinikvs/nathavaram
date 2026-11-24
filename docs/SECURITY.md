# Sign-in security

Four controls sit on top of bcrypt passwords and JWT sessions: a login throttle, uniform login errors, a forced password change, and an owner flag for user management. Examples use synthetic usernames only.

## Login throttle

Applies to every password check: `POST /api/auth/login`, the web `POST /login`, and the current-password check on `POST /api/auth/change-password` and the web change-password form.

| Setting | Value |
| --- | --- |
| Window | 5 minutes, sliding |
| Limit | 5 failed attempts per client IP **and** 5 per username |
| Username key | trimmed and case-folded, so `RateUser1` and ` rateuser1 ` share one count |
| Blocked response | HTTP 429 with `Retry-After: <seconds until the oldest failure leaves the window>` |
| Reset | A successful login clears that username's failures. The IP's other failures stay |

Once an IP or a username has 5 failures in the window, further attempts get 429 **before** the password is checked, so a correct password does not get through while blocked. Blocked attempts are not counted, so they do not extend the block.

Caveats:

- The counters are in process memory. They do not sync across workers or hosts (each gets its own 5-attempt budget) and they reset on restart. Run one worker, or add a shared store (for example Redis) if you need a hard limit.
- The client IP is the socket peer. `X-Forwarded-For` is ignored because a client can forge it. Behind a reverse proxy every request shares the proxy's address unless the server is started with trusted proxy headers (`uvicorn --proxy-headers --forwarded-allow-ips=<proxy ip>`), so configure that or the per-IP limit will count all users together.
- A per-username limit means anyone can lock a known username out for up to 5 minutes. That is the accepted trade-off.

## Uniform login errors

An unknown username, an inactive account, and a wrong password all return `401` with `{"detail": "Invalid username or password"}` (the web form shows the same text). When no usable account exists the server still runs one bcrypt verification against a dummy hash, so response time does not obviously reveal whether a username exists. It is not a strict constant-time guarantee.

## Forced password change

`users.must_change_password` is true for:

- every newly created user,
- the admin seeded from `ADMIN_USERNAME` / `ADMIN_PASSWORD`,
- any user whose password an owner resets with `PATCH /api/users/{id}`,
- all existing users when migration `0002_user_security_flags` runs.

While it is true:

- Login still succeeds and returns a token (`must_change_password: true` in the response).
- API and sync routes return `403` with `{"detail": "<message>", "code": "password_change_required"}`. Only `POST /api/auth/change-password` and `POST /api/auth/logout` work. `GET /healthz` stays public.
- Web pages redirect (303) to `/change-password`.

`POST /api/auth/change-password` takes `{"current_password", "new_password"}`. The new password must be 10 to 200 characters and different from the current one. A wrong current password or an identical new password returns 400; a short one returns 422. On success the flag is cleared, every earlier token for that user is revoked (see Session revocation), and the response carries a fresh `access_token`, `expires_in` and `csrf_token`. When the request used the session cookie, the cookie is re-issued too, so the browser stays signed in. Clients should check `code == "password_change_required"` and send the user to a change-password screen.

Created users: `POST /api/users` and the web form `POST /users` always store `must_change_password=true`; a client-supplied value is rejected (422), not honored. The 10-character minimum is enforced by the `UserCreate` schema and again in `create_user`. Web pages that would otherwise skip the gate (`GET /`, `GET /login` when already signed in, `next=` targets) redirect to `/change-password` while the flag is set. Allowed while flagged: login, logout, `GET/POST /change-password`, `POST /api/auth/change-password`, `/static`, `/healthz`.

Every signed-in user (any role) has a "Change password" link in the web sidebar (ADMIN section) and may change their password at any time, not only on first login.

## Sidebar navigation

The web UI has no top nav links; navigation lives in the left sidebar (`app/web/nav.py`). Master Data, Survey Report, Import and Users are shown to admins only; Deleted voters to managers and admins. Hiding a link is cosmetic: each route still enforces its role on the server (403 for non-admins). "Coming soon" stub pages require login, and the admin ones (Survey Report) are admin-only too.

## Roles

Three roles on `users.role`, enforced in `app/permissions.py` and on every route and sync push (navigation links are cosmetic):

| Capability | `user` | `manager` | `admin` (owner included) |
|---|---|---|---|
| Sign in, search, view, create voters | yes | yes | yes |
| Edit `USER_EDITABLE_FIELDS` on a voter | yes | yes | yes |
| Edit Caste, FirstName, LastName, Gender, Mandal, Constituency | no | no | yes |
| Soft delete (single and bulk) | yes | yes | yes |
| Deleted voters page, restore | no | yes | yes |
| Hard delete (permanent, strips personal data) | no | yes | yes |
| Import, export, Master Data | no | no | yes |
| User accounts | no | no | yes (owner protections below) |

Hard delete is never reachable through sync. A `user` cannot restore through sync either.

## Account management (admin)

Admins (owner included) manage accounts. Cookie-authenticated mutations need the CSRF token; Bearer requests are exempt. Non-admins (`user`, `manager`) get 403.

Owner protections (enforced in `app/services/users.py`):

- Only the owner can change, reset the password of, deactivate or delete the owner account. Another admin gets 403.
- The owner account must stay `admin`; a role change to `manager` or `user` answers 400.
- The owner cannot be deleted, and nobody can deactivate or delete themself.
- At least one active admin must remain.

| Endpoint | Purpose |
|---|---|
| `GET /api/users` | List users (soft-deleted ones are hidden) |
| `POST /api/users` | Create (`must_change_password=true`) |
| `PATCH /api/users/{id}` | Change role / active flag / password |
| `POST /api/users/{id}/reset-password` | Body `{"new_password"}` (10+ chars). Sets a temporary password, `must_change_password=true`, bumps `token_version`, clears the username's login throttle |
| `DELETE /api/users/{id}` | Soft delete (below) |
| `GET/POST /users`, `POST /users/{id}/reset-password`, `POST /users/{id}/delete` | Web equivalents. Delete asks for confirmation in the browser and requires `confirm=yes` |

Choices:

- An owner cannot reset their own password through these endpoints (including `PATCH`); they use Change password, which requires the current password.
- Delete is a soft delete. Voters carry no user foreign keys, but the row is kept for auditability and so the unique username stays reserved (creating the same name again returns 409). Delete sets `is_active=false`, stamps `deleted_at`, and bumps `token_version`. Deleted users cannot log in and are hidden from the management list. An owner cannot delete themself or another owner.
- Audit: reset, delete and password changes write a structured `nathavaram.audit` log line (`action`, actor id/username, target id/username). No passwords are logged. There is no audit table.

## Session revocation

`users.token_version` (integer, migration `0003_token_version`) is embedded in each JWT as the `tv` claim and checked on every request, cookie or Bearer. A missing claim, a mismatch, or an inactive user gives 401, like a bad token. The version is incremented on owner reset, owner delete or deactivation, and the user's own successful password change. Tokens issued before migration 0003 have no claim and stop working once; users sign in again.

## Owner

`users.is_owner` marks the owner account. Only the owner may change or reset that account; admins manage every other account (`/users`, `/api/users`). It is a stored flag, not a username check.

- It is set only on the admin created from `ADMIN_USERNAME`. If that user already exists at startup (a database from before the flag), startup marks it as owner and leaves its password unchanged.
- It cannot be set through the API. Admins created later are not owners.
- Other admins manage user accounts too, except the owner account (see Account management above). Non-admins get 403 on the user-management routes.
- Pointing `ADMIN_USERNAME` at a different name later adds a second owner; the earlier owner keeps the flag.
