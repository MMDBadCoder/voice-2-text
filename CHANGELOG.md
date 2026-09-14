# Changelog

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

See [known limits](docs/ARCHITECTURE.md#known-limits-in-010) before deployment.
