# Architecture and API

For detailed system, sequence, lifecycle and deployment diagrams, see the
[visual architecture guide](arcetecture.md).

```mermaid
flowchart LR
    Browser[Persian RTL browser UI] --> API[FastAPI]
    API --> DB[(SQLite metadata)]
    API --> Files[Local audio and results]
    API --> Redis[(Redis / RQ)]
    Redis --> Supervisor[Queue supervisor]
    Supervisor --> Child[Isolated recording process]
    Child --> ASR[faster-whisper / CTranslate2]
    Child --> DB
    Child --> Files
```

An upload is validated, stored locally and added to the queue. A supervisor
atomically claims the job and starts a fresh Python interpreter in its own POSIX
process group. The child probes audio, loads a local model, transcribes and
normalizes Persian text, optionally assigns speaker labels, and writes an atomic
JSON result. The browser polls metadata; exports derive from the stored result.

## Cancellation

The API marks cancellation, and the supervisor checks the database and Redis
every 200 ms. It kills the owned processing group and waits for the direct child
to exit before confirming stopped. This interrupts native inference without
waiting for a segment boundary. Model memory is released with the child.

The public job status remains `canceled` while its `stage` is `canceling` until
exit is confirmed. The UI displays “stopping,” keeps polling, and blocks retry.
A queued cancellation prevents a successful supervisor claim. Duplicate queue
deliveries cannot claim a job already running or completed. Conditional SQL
prevents late progress/done callbacks from reviving canceled jobs.

This is not host-crash recovery. A killed supervisor or system outage can leave
stale metadata. Backups and process supervision remain operator responsibilities.

## HTTP API

Interactive API documentation: `/docs`. OpenAPI schema: `/openapi.json`.
There is no authentication; every connected user has access to the same library.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/jobs` | Multipart `file`, optional `title` (max 200 characters), optional `tier`. |
| `GET` | `/api/jobs` | List with `limit` (1–200), `offset`, `status`, `search`. |
| `GET` | `/api/jobs/{id}` | Status, stage, progress and metadata. |
| `GET` | `/api/jobs/{id}/segments` | Transcript segments and source metadata. |
| `GET` | `/api/jobs/{id}/audio` | Original audio if retained. |
| `GET` | `/api/jobs/{id}/download?format=txt` | `txt`, `plain`, `docx`, `srt`, `vtt`, `json`. |
| `POST` | `/api/jobs/{id}/cancel` | Cancel queued/running processing while keeping the recording row. |
| `POST` | `/api/jobs/{id}/retry` | Requeue failed/stopped jobs when audio still exists. |
| `DELETE` | `/api/jobs/{id}` | Stop processing and delete recording, source audio and canonical result. |
| `GET` | `/api/health` | Operational queue, model, CPU, job and disk information. |
| `GET` | `/healthz` | API liveness; does not prove workers or models are ready. |

Example:

```bash
curl -F 'file=@meeting.mp3' \
     -F 'title=جلسهٔ هفتگی' \
     -F 'tier=accurate' \
     http://127.0.0.1:8000/api/jobs
```

A blank title falls back to the original filename. Titles are stored, searchable,
and used for export filenames. Startup adds the nullable title column to older
databases without replacing existing recordings.

## Known limits in 0.1.0

- Linux/POSIX process management; native Windows is unsupported.
- One shared library, no accounts or per-recording permissions.
- No streaming transcript or live microphone recording; upload completed files.
- Progress advances at decoder segment boundaries; it is not an ETA.
- No built-in supervisor-crash recovery or transactional database/queue enqueue.
- Browser library displays up to 200 matching records; API supports pagination.
- Generated Word files may remain cached in the results directory after deletion;
  operators should include the results directory in storage management.
- Optional diarization is not covered by the automated model-free tests.
- SQLite on a single host is the supported documented storage arrangement.
