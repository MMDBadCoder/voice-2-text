# Deployment

## Docker Compose: connected installation

Clone the repository, copy `.env.example` to `.env`, and install a model using
[Setup](SETUP.md). The following commands assume one worker and the accurate tier:

```bash
mkdir -p data models
sudo chown 10001:10001 data
# If migrating existing data, ensure all its files are writable by UID 10001 too.
docker compose build
docker compose up -d --scale worker=1
docker compose ps
docker compose logs --tail=100 api worker
curl http://127.0.0.1:8000/healthz
```

The Dockerfile downloads Python packages while building; running containers use
local models. The non-root app user has UID 10001. `data/` holds the SQLite database,
uploaded audio, and transcripts; `models/` is mounted read-only. Redis persists
its queue state in the `redis-data` named volume. Do not remove this volume during
routine restarts.

The `.env` `HOST` setting defaults to `127.0.0.1`. Set it to a private interface or
`0.0.0.0` only if other machines should reach the service. `PORT` defaults to 8000.
Compose fixes the in-container Redis and data paths, so the same `.env` can be
used by the local launcher.

Keep the worker scale and configuration consistent. For example, with at least
10 logical cores and sufficient RAM:

```dotenv
WORKER_COUNT=2
WORKER_CPU_THREADS=4
RESERVED_CORES=2
WORKER_CPU_LIMIT=4.0
WORKER_MEM_LIMIT=6g
```

```bash
docker compose up -d --scale worker=2
```

Compose does not infer `--scale` from `WORKER_COUNT`; explicitly pass the same
count. Docker resource limits are per worker container. API health checks use
`/healthz`; the worker intentionally has no HTTP health check. Check queue state
and worker logs with `/api/health` and `docker compose logs worker`.

To stop:

```bash
docker compose stop --timeout 60
```

Prefer stopping after active recordings complete. The supervisor confirms normal
UI cancellation, but a host crash or forced container shutdown may leave jobs
requiring manual recovery; this release does not provide durable crash recovery.

## Offline deployment

The transfer must include **the project source and the bundle**, not just the
bundle. Match the connected preparation machine's Linux distribution, CPU
architecture and Python minor version to the target. For Docker, prepare wheels
inside the same `python:3.11-slim` base environment or build and transfer the
complete app image while online.

### Option A: transfer ready-built Docker images (recommended for Docker)

On the connected machine, prepare the model bundle and build the app:

```bash
./scripts/fetch-offline-deps.sh
docker compose build
docker pull redis:7-alpine
mkdir -p bundle/images
docker save voice-2-text:0.1.0 redis:7-alpine -o bundle/images/runtime.tar
```

Transfer the source tree and `bundle/` to the target. On the target:

```bash
docker load -i bundle/images/runtime.tar
cp .env.example .env
mkdir -p data models
cp -a bundle/models/. models/
sudo chown 10001:10001 data
docker compose up -d --no-build --pull never --scale worker=1
```

Edit `.env` for your hardware before starting. This option does not need Python,
PyPI or a compiler on the offline host; Docker must already be installed.

### Option B: install Python wheels on the offline host

On the connected machine:

```bash
./scripts/fetch-offline-deps.sh
```

Transfer the source and bundle. Python 3.11 with `venv`, Redis and OS prerequisites
must already be installed on the offline host. From the project root:

```bash
./scripts/install-offline.sh
cp .env.example .env
./scripts/run-local.sh
```

The installer uses `pip --no-index`, copies models and fonts, and preserves an
installed requirements lock. Redis must be running before the launcher starts.

### Option C: build a Docker image offline from wheels

Prepare compatible wheels and base images with `--images` on the connected
machine. Transfer the source and bundle, load the images, then run:

```bash
docker load -i bundle/images/python-3.11-slim.tar
docker load -i bundle/images/redis-7-alpine.tar
docker build --network=none --pull=false -f Dockerfile.offline -t voice-2-text:0.1.0 .
```

Install model directories and configure `.env` as above; start with
`docker compose up -d --no-build --pull never --scale worker=1`. The offline
Dockerfile requires `bundle/requirements.lock.txt` and `bundle/wheels/`.

## HTTPS and access control

This release has no accounts, permissions, or user isolation. Anyone with access
can read, upload, cancel, retry, and delete recordings. Keep Redis private too.
For access outside a trusted network, place an authenticated HTTPS reverse proxy
in front of the localhost API. For example, an existing Nginx TLS virtual host
can proxy the app like this:

```nginx
# Inside a server block with your own TLS certificate configuration:
client_max_body_size 500m;
auth_basic "Recordings";
auth_basic_user_file /etc/nginx/voice-users;
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 120s;
}
```

Create the password file using your server's standard administration process;
never store it in Git. Match the proxy upload limit to `MAX_UPLOAD_MB`. Queued
transcription is asynchronous, so the proxy need not wait for an entire meeting.

## Backups, retention and upgrades

For a consistent simple backup, wait for active jobs to finish and stop the stack.
Back up `data/`, `.env` privately, and the Redis volume with your Docker volume
backup tooling. Copy `models/` too if downloading them again would be difficult.
Restart the same stack afterward. SQLite's WAL files belong with the database;
do not copy only a live `jobs.db` file while writes are occurring.

`DELETE_AUDIO_AFTER_TRANSCRIBE=true` removes source audio after successful
transcription and disables later playback. `RETENTION_DAYS=0` disables retention.
A retention sweep removes **both recordings and transcripts**, including old
rows; schedule it only while no jobs are active:

```bash
./scripts/retention-sweep.sh
```

Review retention settings before using it. It is not enabled on a timer by default.

Before upgrading, make a backup, stop the services after active work finishes,
check out the desired Git tag, rebuild the image, and restart. Startup applies
additive schema changes (including optional recording titles). Restore both
matching code and data from backup if rolling back an incompatible migration.

## Release 0.1.0 validation and limits

The automated suite covers 49 tests, including subprocess completion/cancellation,
exports and schema migration. Browser and live CPU-model checks were performed.
Optional diarization and air-gapped installation on other distributions still
require validation in your target environment. Dependency floors and model URLs
can resolve differently over time: archive the generated lock, wheels, images and
model revisions when you need a reproducible deployment.
