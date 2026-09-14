# Accounts, Bale and session recording

## Configure the first administrator

Before starting the updated API, configure these values in private `.env`:

```dotenv
BOOTSTRAP_ADMIN_PHONE=YOUR_11_DIGIT_NUMBER_STARTING_WITH_09
BOOTSTRAP_ADMIN_PASSWORD=YOUR_INITIAL_PASSWORD
BALE_BOT_TOKEN=YOUR_BALE_BOT_TOKEN
BALE_BOT_USERNAME=YOUR_BOT_USERNAME_WITHOUT_AT
```

The administrator is created once with an Argon2 password hash. Existing accounts'
passwords are never overwritten on restart. Existing unowned recordings migrate
to this administrator's private workspace. Back up the database before upgrading.
The initial admin can sign in by password without Bale verification. The bootstrap
phone is a deployment setting; it does not become a public registration shortcut.

Do not put tokens, passwords or actual account details in Git. The server creates
`data/.auth-secret` with restrictive permissions for code hashing. Back it up with
the database, and share the same data directory between API and Bale processes.
The application supports the documented single-host SQLite deployment.

## Start the Bale verification service

For a local installation, `scripts/run-local.sh` now starts the Bale process when
`BALE_BOT_TOKEN` is set. For direct process management, load `.env` and run:

```bash
python -m app.bale
```

For Compose:

```bash
docker compose --profile accounts up -d --build --scale worker=1
```

The bot uses long polling, so no public webhook is needed. Run exactly one poller
per bot/data directory. A filesystem lock prevents duplicate local pollers, and
the update offset persists in SQLite. If the bot has an existing webhook, the
poller stops with a configuration error instead of silently replacing it. Remove
that webhook through your bot management process before switching to long polling.

Bale connectivity is required for verification codes. Password login and audio
processing do not send audio or transcripts to Bale. A bot cannot identify an
unlinked phone merely from its number: the user must open the challenge link and
share their own contact using the bot's contact-request button. Forwarded contacts,
contacts without a matching sender identity and mismatched phone numbers are
rejected. If a client does not supply the contact's Bale user ID, verification
fails safely rather than accepting an unverified phone number.

## Signup, login and approval

- `/signup`: full name, 11-digit `09` phone and password; verify a six-digit Bale code.
- `/pending`: waiting screen until an admin approves the account.
- `/login`: password or Bale-code login.
- `/admin`: administrator-only search, approval and suspension of users.
- `/forgot-password`: verify a Bale code and set a new password.
- `/account`: change password using the current password.

Phones entered with Persian or Arabic digits are normalized. Website inputs must
still be exactly 11 digits starting with `09`. Contact phone numbers from Bale may
use the `+98` country prefix and are normalized separately.

Codes expire after five minutes by default, permit five verification attempts,
are single-use and are bound to their purpose. Requests have a resend cooldown
and per-phone/IP rate limits. A successful password reset revokes all logins;
a signed-in password change revokes other logins and rotates the current one.
Suspending a user revokes their logins and asks active jobs to stop. Administrators
manage account access but cannot read another user's private recordings.

## HTTPS for browser recording

Remote microphone access requires a trusted HTTPS origin. Configure the public
origin and secure cookies behind your TLS reverse proxy:

```dotenv
PUBLIC_BASE_URL=https://voice.example.org
AUTH_COOKIE_SECURE=true
```

Use your real domain. Forward the original host and scheme to the app. Origin
checks and CSRF headers protect authenticated mutations. Setting secure cookies
while still visiting an HTTP URL prevents browsers from sending those cookies.
For a localhost-only development installation, use `AUTH_COOKIE_SECURE=false`.

The recorder uses browser microphone permission and MediaRecorder, then decodes
and encodes MP3 locally in a Web Worker using the bundled LGPL-licensed lamejs.
The resulting file is an actual MP3, not a renamed WebM. The user can preview and
download it before saving it to a session. Denied microphone permission and
unsupported/insecure browsers offer file upload as a fallback. Each recording is
limited to ten minutes to bound browser memory; add additional clips as needed.
Do not close the tab while a local recording or upload is unsaved.

## Multi-file sessions

An open session accepts ordered audio clips and does not start ASR. Uploads use
the existing supported formats and per-file size limit. Additional limits:

```dotenv
MAX_SESSION_FILES=50
MAX_SESSION_MB=2000
```

“Close session and generate text” locks editing, streams the clips into one mono
MP3, and transcribes it in an isolated child process. Transcript timestamps refer
to that continuous audio. A failed/stopped session can retry or reopen for editing
once the previous process exits. Files cannot be added/reordered/removed while
closed or processing. Deleting a session removes its clips and generated results.
`DELETE_AUDIO_AFTER_TRANSCRIBE=true` also removes source clips on completion.

## API clients

Sign in with JSON at `POST /api/auth/login/password` or the Bale-code flow.
Keep the HttpOnly session cookie and the response's `csrf_token`. Include that
token as `X-CSRF-Token` on POST/DELETE requests. `GET /api/auth/me` returns the
current account and CSRF token. Workspace endpoints return 401 without a login,
403 without approval, and 404 for a recording owned by somebody else.

See [product and design specification](ACCOUNTS_AND_SESSIONS.md) for the detailed
flows, and [API reference](ARCHITECTURE.md) for session endpoints.
