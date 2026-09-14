# Configuration

Copy `.env.example` to `.env`. Restart both the API and workers after changing
settings. The local launcher loads this file, and Compose supplies it to each
container. Never publish `.env` or source it from an untrusted source.

## CPU and memory

Startup enforces:

```text
WORKER_COUNT × WORKER_CPU_THREADS <= logical cores − RESERVED_CORES
```

| Setting | Default example | Meaning |
| --- | --- | --- |
| `WORKER_COUNT` | `1` | Intended number of queue supervisors; match Compose scale. |
| `WORKER_CPU_THREADS` | `2` | Inference threads per active recording. |
| `RESERVED_CORES` | `1` | CPU budget left for API, Redis and other work. |
| `WORKER_JOB_TIMEOUT` | `14400` | Maximum job time in seconds. |
| `WORKER_CPU_LIMIT` | `4.0` | Docker CPU ceiling per worker. Keep at least the thread count. |
| `WORKER_MEM_LIMIT` | `6g` | Docker RAM ceiling per worker. |
| `API_CPU_LIMIT` / `API_MEM_LIMIT` | `1.5` / `2g` | Docker API limits. |

On a 2-core host use one worker, one thread, and one reserved core. Such a host
may be slow. Start with 8 GB RAM for a single accurate worker and monitor actual
memory usage. More workers multiply model and decoded-audio memory usage.
Long recordings can require more memory than short clips. The app does not
estimate a safe RAM budget automatically.

Each recording uses a fresh interpreter and reloads its model. Workers do not
keep models warm between jobs. Avoid setting OpenMP/BLAS thread variables higher
than `WORKER_CPU_THREADS` in the surrounding environment.

## Models and transcription

| Setting | Meaning |
| --- | --- |
| `ASR_BACKEND` | `faster_whisper` for real text; `stub` for fabricated demo text. |
| `ENABLED_TIERS` | Comma-separated `accurate`, `fast`, or both. Every enabled model must exist. |
| `DEFAULT_TIER` | Initially selected upload tier; must be enabled. |
| `MODEL_ACCURATE` | Local CTranslate2 accurate-model directory. |
| `MODEL_FAST` | Local CTranslate2 turbo-model directory. |
| `COMPUTE_TYPE` | `int8` by default. |
| `LANGUAGE` | `fa` for Persian. |
| `BEAM_SIZE` | Decoder beam size; default 5. |
| `VAD_ENABLED` | Remove silence before decoding. |
| `VAD_MIN_SILENCE_MS` / `VAD_SPEECH_PAD_MS` | VAD timing controls. |
| `DIARIZATION_ENABLED` | Optional speaker labels; requires extra models/packages. |

`INITIAL_PROMPT` exists in the configuration but is currently not passed to the
ASR decoder. `WARM_UP_ALL_TIERS` is a legacy setting and has no effect on the
supervised worker entry point in this release.

The runtime loads model files locally; it does not fetch missing weights. See
[Setup](SETUP.md) for model preparation. The source repository does not include
weights or grant a license to redistribute them.

## Web, storage and queue

| Setting | Meaning |
| --- | --- |
| `HOST`, `PORT` | Local listen address and port; Compose uses HOST for published-port binding. |
| `APP_TITLE` | HTML/API application title; UI branding is currently “واژه”. |
| `REDIS_URL` | Host Redis URL; Compose overrides with its internal Redis service. |
| `QUEUE_NAME` | Queue shared by the API and all workers; default `transcription`. |
| `DATA_DIR` | Uploads, result files and default database directory. |
| `DB_URL` | SQLAlchemy database URL; SQLite is the documented/tested deployment. |
| `MAX_UPLOAD_MB` | Maximum uploaded file size, default 500 MiB. |
| `ALLOWED_EXTENSIONS` | Accepted filename extensions. Actual decoding can still fail. |
| `DELETE_AUDIO_AFTER_TRANSCRIBE` | Delete source audio after successful transcription. |
| `RETENTION_DAYS` | Age threshold for manual retention sweep; 0 means disabled. |

Default data layout:

```text
data/
  jobs.db
  audio/<job-id>.<extension>
  results/<job-id>.json
models/
  whisper-fa-large-v3-int8/
  whisper-fa-turbo-int8/       # optional
```

Do not expose `data/`, Redis or the health endpoint's operational details to
untrusted users. Disk quotas, authentication, and backup scheduling are deployment
responsibilities, not application features.
