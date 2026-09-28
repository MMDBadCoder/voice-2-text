# Changelog

## 0.2.0 — 2026-09-28

### Added

- Phone-based accounts with Bale verification, password or code login, password
  recovery/change, administrator approval and private workspaces.
- Titled sessions with multiple ordered audio files, browser recording and
  live microphone transcription using the installed local Whisper model.
- Editable live transcripts, live-only finalization and downloadable exports.
- Modern Persian RTL studio, responsive mobile navigation and dark/light themes.
- Updated architecture, account deployment and workflow audit documentation.

### Fixed

- Wait for the final transcription acknowledgement before stopping capture.
- Preserve microphone text when exporting sessions that also contain uploads.
- Refresh completed file transcripts without interrupting active edits.
- Preserve uploads when queueing fails and allow processing retries.
- Prevent duplicate upload runners and premature session finalization.
- Recheck access on live connections and reject malformed audio frames.
- Remove transcript blocks when clips, sessions or expired records are deleted.
- Restore the deployed worker using a dedicated localhost-only Redis instance.

### Upgrade

Back up the database and audio/results directories before upgrading. Configure
bootstrap administrator credentials and Bale settings in private `.env` before
starting. Existing unowned recordings are assigned to the bootstrap administrator;
existing account passwords are preserved. Enable the Compose `accounts` profile
for the Bale polling service. See [account setup](docs/ACCOUNTS_SETUP.md).

### Validation and limits

- 92 automated tests passed.
- Browser microphone → real local model → saved text → export verified.
- Production upload, queue, worker, finalization and export checks passed.
- Docker build and Python 3.11 container startup verified.
- Live text appears after pauses and inference, not word by word.
- Remote HTTP recording requires a browser trusted-origin exception; HTTPS
  or localhost also work.
- Bale delivery is mocked in automated tests; actual contact sharing requires
  verification with a real Bale client and network access to Bale.

See the [workflow audit](docs/STUDIO_REDESIGN.md) for detailed behavior and limits.

## 0.1.0 — 2026-09-14

Initial public release.

### Features

- Offline Persian transcription using CPU-based faster-whisper/CTranslate2.
- Accurate model and optional converted turbo tier.
- Persian RTL recording library with local Vazirmatn fonts and responsive layout.
- Optional titles, search, status filters, upload and transcription progress.
- Transcript reader, source audio playback, optional clickable timestamps and copy.
- TXT, Word, SRT, VTT and JSON exports.
- Isolated processing with cancellation, confirmed stopping state, and retry.
- Additive title-column migration for existing installations.
- Optional speaker diarization and manual retention controls.

### Deployment and documentation

- Local setup and Docker Compose deployment guides.
- Separate connected and offline Dockerfiles.
- Offline wheel/model bundle preparation with matching Python interpreter.
- Tokenizer generation for legacy Whisper checkpoints.
- Configuration, access-control, backup, upgrade and troubleshooting guidance.

### Validation

- 49 automated tests for the application, including real child-process tests.
- Desktop/mobile browser checks for primary user flows.
- Live accurate-model cancellation, successful retry and Word export.

See [known limits](docs/ARCHITECTURE.md#known-limits) before deployment.
