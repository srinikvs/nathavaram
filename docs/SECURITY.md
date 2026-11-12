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

`POST /api/auth/change-password` takes `{"current_password", "new_password"}`. The new password must be 10 to 200 characters and different from the current one. A wrong current password or an identical new password returns 400; a short one returns 422. On success the flag is cleared and the existing token keeps working. Clients should check `code == "password_change_required"` and send the user to a change-password screen.

## Owner

`users.is_owner` marks who may create and manage users (`/users`, `/api/users`: list, create, patch, password reset). It is a stored flag, not a username check.

- It is set only on the admin created from `ADMIN_USERNAME`. If that user already exists at startup (a database from before the flag), startup marks it as owner and leaves its password unchanged.
- It cannot be set through the API. Admins created later are not owners.
- Other admins keep bulk export, import and delete, and get 403 on the user-management routes.
- Pointing `ADMIN_USERNAME` at a different name later adds a second owner; the earlier owner keeps the flag.
