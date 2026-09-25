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

## 0.3.0 — رونویسی زنده و بازطراحی

### رونویسی زندهٔ میکروفن / Live microphone transcription
- جلسهٔ باز اکنون یک متن روبه‌رشد دارد که با صحبت کردن کامل می‌شود.
- The browser segments speech itself: it watches microphone energy and ships
  one complete utterance per pause. Whisper is not a streaming model, so a
  sliding window would re-decode the same audio and rewrite words under the
  reader; cutting at a pause means each piece is decoded once and never changes.
- Speech shorter than the minimum is **carried over into the next utterance**
  rather than discarded, so a lone "بله" is never silently lost. A carried
  fragment is shipped on its own after 6 s if no further speech follows.
- Whisper pads every call to a 30-second window, so a 2-second chunk costs
  nearly what a 10-second chunk costs. Utterance targets are therefore long
  (2.5–18 s), which is both faster per second of audio and more accurate.
- Live work bypasses the RQ queue and runs in the API process against its own
  warm model, bounded by a semaphore; a queued live chunk would sit behind a
  40-minute meeting and "live" would mean nothing.
- Blocks are editable and deletable inline; each is persisted as it arrives.
- Uploaded files reserve their transcript slot immediately and are filled in by
  the queue, so a file lands where the user dropped it rather than wherever the
  queue happened to finish.
- Live uses the session's own tier, so live text and the final transcript agree.

### طراحی / Design
- Rebuilt design system: neutral oklch palette with a single accent, borders
  instead of shadows, consistent radii, and a 20/16/14/12 type scale.
- Status is a dot and a word, not a coloured pill on every row.
- Inline SVG icon sprite replaces the `＋ ⌄ ← ↓` glyphs.
- Removed the "eyebrow" labels and explanatory paragraphs above every section.
- Session tiles compacted from five stacked lines to two.
- Motion: 150 ms hover lift, 160 ms press, 380 ms row entry staggered 70 ms.
- Full dark mode, and `prefers-reduced-motion` honoured.

### اصلاح‌ها / Fixes
- The minimum-utterance guard sat after the backend switch, so a stray click
  created an empty transcript block under the stub backend.
- Repetition collapse now triggers at three repeats instead of five; live
  chunks are short and produced "بگویم بگویم بگویم" in a real run.
