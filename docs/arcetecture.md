# Visual architecture

Voice to Text (واژه) is a single-host, offline Persian transcription application.
FastAPI serves the web interface and manages recordings; Redis/RQ schedules work;
a separate child process performs CPU inference for each recording. SQLite and
local files preserve the library across application restarts.

The diagrams describe the current implementation. GitHub renders the Mermaid
blocks directly. See [Setup](SETUP.md), [Deployment](DEPLOYMENT.md),
[Configuration](CONFIGURATION.md), and the [API reference](ARCHITECTURE.md)
for commands, settings and endpoint details.

## 1. System overview

```mermaid
flowchart TB
    Browser["Browser: Persian RTL library and transcript reader"]

    subgraph Host["Application host: offline runtime"]
        API["FastAPI: pages, uploads, status and exports"]
        Redis[("Redis: RQ queue and cancellation flags")]
        Worker["RQ SimpleWorker: job supervisor"]
        DB[("SQLite: recording metadata and progress")]
        Audio["Local audio files"]
        Results["Canonical JSON transcripts"]
        Models["Local CTranslate2 model files"]

        subgraph Group["Owned process group: one recording"]
            Child["Python child: app.job_process"]
            ASR["faster-whisper / CTranslate2: CPU int8 inference"]
            Normalize["Persian normalization"]
            Diarize["Optional speaker diarization"]
            Child --> ASR --> Normalize --> Diarize
        end

        API -->|"enqueue job ID, request cancellation"| Redis
        Worker -->|"consume jobs, check cancellation"| Redis
        Worker -->|"claim job, monitor state"| DB
        Worker -->|"spawn, supervise and reap"| Child
        API -->|"read and write metadata"| DB
        API -->|"store uploads, serve audio"| Audio
        API -->|"read transcript, generate exports"| Results
        Audio -->|"decode"| Child
        Models -->|"load per recording"| ASR
        Child -->|"progress and final status"| DB
        Child -->|"atomic result write"| Results
    end

    Browser -->|"HTTP uploads, polling and downloads"| API
    API -->|"HTML, JSON, audio and exports"| Browser
```

The API accepts uploads without waiting for transcription. Audio bytes and model
weights stay in local storage; the queue carries a job identifier. The browser
polls job metadata rather than keeping a long-running transcription request open.
Fonts, styles and JavaScript are bundled locally, so the runtime needs no CDN.

## 2. From upload to transcript

```mermaid
sequenceDiagram
    actor User
    participant UI as Browser
    participant API as FastAPI
    participant Disk as Local files
    participant DB as SQLite
    participant Q as Redis / RQ
    participant S as Supervisor
    participant C as Recording child

    User->>UI: Select audio, optional title and tier
    UI->>API: POST /api/jobs (multipart upload)
    API->>Disk: Validate and save audio
    API->>DB: Insert queued recording
    API->>Q: Enqueue run_isolated(job_id)
    API-->>UI: 201 Created with job metadata
    S->>Q: Receive queued work
    S->>DB: Atomically claim queued job as running
    S->>C: Start a fresh interpreter and process group
    C->>Disk: Probe audio and load selected local model
    loop Decode segments
        C->>C: Transcribe and normalize Persian text
        C->>DB: Write throttled progress
    end
    opt Diarization enabled
        C->>C: Assign speaker labels
    end
    C->>Disk: Write temporary JSON, then atomically replace result
    C->>DB: Mark done and store final metadata
    C-->>S: Exit, model memory is released
    loop While waiting for completion
        UI->>API: GET /api/jobs/{id}
        API->>DB: Read status and progress
        API-->>UI: Current job metadata
    end
    UI->>API: GET /api/jobs/{id}/segments
    API->>Disk: Read canonical transcript
    API-->>UI: Segments with timestamps
    User->>UI: Read, play audio, copy or download
```

Polling also occurs while the child is decoding; its placement after completion
in the diagram keeps the two flows legible. Progress writes are usually throttled
to one update per 1.5 seconds and depend on decoder segment boundaries. Model
loading and the first segment can take time before measurable progress appears.
The displayed percentage is not a completion-time estimate.

TXT, plain text, SRT, VTT and JSON exports are generated from the canonical result.
Word output is generated into a local `.docx` file. Source audio remains available
for playback unless retention settings or deletion have removed it.

## 3. Cancellation and resource ownership

```mermaid
sequenceDiagram
    actor User
    participant API as FastAPI
    participant DB as SQLite
    participant Q as Redis
    participant S as Supervisor
    participant C as Recording process group

    User->>API: POST /api/jobs/{id}/cancel
    API->>Q: Set cancellation flag
    API->>DB: Set status=canceled, stage=canceling for running job
    API->>Q: Cancel RQ job entry
    API-->>User: Stopping, retry remains unavailable
    S->>DB: Observe canceled status
    Note over S,Q: Supervisor also checks the Redis flag
    S->>C: SIGKILL the owned process group
    S->>C: Wait for the direct child to exit
    S->>DB: Set stage=canceled and finished_at
    Note over S,DB: Cleanup also removes partial result and clears flag
    User->>API: Poll job status
    API-->>User: Fully stopped, retry available if audio exists
```

The supervisor normally checks every 200 ms; this is a polling interval, not a
hard end-to-end latency guarantee. It can stop native decoding, audio probing and
diarization without waiting for the next segment. Cooperative checks in the
transcription code provide a secondary cancellation path.

A running cancellation has two observable phases:

| API status | API stage | Meaning |
| --- | --- | --- |
| `canceled` | `canceling` | Stop requested; the supervisor has not yet confirmed exit. |
| `canceled` | `canceled` | Stopped; a retry can proceed if source audio remains. |

Each supervisor handles one recording at a time. Its child owns the loaded model
and inference threads. A fresh interpreter avoids forking an already initialized
native inference runtime. This design reloads the model for each recording, but
releases its memory after completion or cancellation. Idle supervisors hold no
warm model cache.

Deleting a recording also signals cancellation. The API removes its row, audio
and canonical result; the supervisor treats a missing row as a reason to stop.
The normal cancel action keeps the recording row and source audio for retry.

## 4. Recording lifecycle

```mermaid
stateDiagram-v2
    [*] --> Queued: Upload accepted
    Queued --> Running: Atomic supervisor claim
    Queued --> Stopped: Cancel before claim
    Queued --> Failed: Enqueue failure
    Running --> Done: Result saved
    Running --> Failed: Processing error or timeout
    Running --> Stopping: Cancellation requested
    Stopping --> Stopped: Child exit confirmed
    Stopped --> Queued: Retry with retained audio
    Failed --> Queued: Retry with retained audio
    Done --> Deleted: Delete recording
    Queued --> Deleted: Delete recording
    Running --> Deleted: Delete and signal supervisor
    Stopping --> Deleted: Delete recording
    Stopped --> Deleted: Delete recording
    Failed --> Deleted: Delete recording
    Deleted --> [*]
```

`Stopping`, `Stopped` and `Deleted` are conceptual labels: stopping/stopped both
use API status `canceled`, while deletion removes the database row entirely.
Deletion during processing still requires the supervisor to terminate the child.

An atomic `queued` → `running` database update prevents duplicate queue deliveries
from starting a second decoder for an already claimed job. Conditional updates
prevent late progress or completion callbacks from reviving canceled recordings.
Database writes, queue operations and file writes are separate operations; the
whole pipeline is not one cross-system transaction.

## 5. Deployment topology

```mermaid
flowchart LR
    Browser["Browser"]
    Proxy["Optional authenticated HTTPS reverse proxy"]

    subgraph Machine["Single Linux host"]
        subgraph Compose["Docker Compose network"]
            API["API container: internal port 8000"]
            Redis[("Redis container: internal port 6379")]
            W1["Worker container 1: supervisor + active child"]
            WN["Worker container N: supervisor + active child"]
            API --> Redis
            W1 --> Redis
            WN --> Redis
        end
        Data["Shared read/write data directory"]
        Models["Shared read-only models directory"]
        RedisVolume["Redis persistence volume"]
        API --> Data
        W1 --> Data
        WN --> Data
        Models --> W1
        Models --> WN
        Redis --> RedisVolume
    end

    Browser --> Proxy --> API
    Browser -.->|"Direct localhost or trusted-network access"| API
```

The local launcher uses the same API and worker modules as separate host
processes. Docker Compose packages them as containers with per-service resource
limits. Set `--scale worker=N` to match `WORKER_COUNT`; the application enforces:

```text
WORKER_COUNT × WORKER_CPU_THREADS <= logical cores − RESERVED_CORES
```

More workers allow simultaneous recordings and multiply inference memory usage.
SQLite and uploaded files are shared locally; a multi-host cluster is not the
documented deployment. Redis is internal to the Compose network, while the API
port is published on the address configured by `HOST` and `PORT`.

## 6. Storage and code map

| Responsibility | Implementation | Durable data |
| --- | --- | --- |
| Pages, API and upload orchestration | [`app/main.py`](../app/main.py) | Recording metadata via the database |
| RTL interface, polling and reading controls | [`app/templates/`](../app/templates/), [`app/static/app.js`](../app/static/app.js) | None in the browser required for job execution |
| Configuration and CPU budget | [`app/config.py`](../app/config.py) | Private `.env` deployment settings |
| Metadata and additive schema migration | [`app/db.py`](../app/db.py) | `data/jobs.db` |
| Upload validation and filenames | [`app/storage.py`](../app/storage.py) | `data/audio/<job-id>.<extension>` |
| Queue and cancellation flags | [`app/queue.py`](../app/queue.py) | Redis queue state |
| Worker startup | [`app/worker.py`](../app/worker.py) | None |
| Claim, supervision, cleanup and transcription task | [`app/tasks.py`](../app/tasks.py) | Job status and result metadata |
| Child process entry point | [`app/job_process.py`](../app/job_process.py) | None |
| Model loading and inference | [`app/asr.py`](../app/asr.py) | Preinstalled model files under `models/` |
| Text cleanup and speaker labels | [`app/normalize.py`](../app/normalize.py), [`app/diarize.py`](../app/diarize.py) | Normalized segments in the canonical result |
| Atomic transcript persistence | [`app/results.py`](../app/results.py) | `data/results/<job-id>.json` |
| Download generation | [`app/exporters.py`](../app/exporters.py) | Word exports may be cached locally |

SQLite stores titles, filenames, sizes, model tiers, status, progress, timing and
summary counts. JSON stores transcript segments, timestamps, language and result
metadata. An optional title is separate from the original and stored filenames;
a missing title falls back to the original filename in the UI.

## 7. Operational boundaries

The application uses approved accounts and owner-scoped sessions. Administrators
manage access but cannot read other users’ audio. Use HTTPS for remote access. Online preparation downloads dependencies
and weights; transcription uses the installed local files.

The supervisor handles normal cancellation and task failures, but there is no
complete recovery mechanism for host crashes or a supervisor killed unexpectedly.
`/healthz` checks API liveness; `/api/health` reports queue, worker and resource
information without proving that a particular model can transcribe successfully.
Keep backups and validate models on the target machine. See the
[known limits](ARCHITECTURE.md#known-limits) and
[deployment guide](DEPLOYMENT.md) for retention and recovery considerations.

## 8. Accounts and multi-clip sessions

```mermaid
flowchart LR
    Browser --> Auth[Cookie authentication and CSRF]
    Auth --> Approval[Admin approval and ownership checks]
    Approval --> Session[Open session]
    Session --> Clips[Ordered uploads and browser MP3 recordings]
    Clips --> Close[Close and lock session]
    Close --> Queue[Redis queue]
    Queue --> Child[Isolated assembly and ASR process]
    Child --> Result[Combined audio and transcript]
    Browser --> Challenge[Phone and purpose-bound challenge]
    Challenge --> Bale[Bale bot contact verification and code]
    Bale --> Account[Signup or login or password reset]
    Account --> Auth
```

SQLite now also holds users, hashed login tokens, verification challenges, Bale
identities, rate limits, approval audit records and ordered audio clips. Existing
unowned jobs are assigned to the bootstrap administrator during migration.
Open sessions do not consume worker resources. Closing streams their clips into
one MP3 before the existing transcription pipeline. Cancellation covers assembly
and inference in the same owned process group.

See [design and lifecycle diagrams](ACCOUNTS_AND_SESSIONS.md) and
[configuration and deployment](ACCOUNTS_SETUP.md).
