# Studio redesign and workflow audit

## Direction

The interface uses a Persian RTL studio layout: permanent desktop navigation,
a compact mobile drawer, a workspace with two clear starting actions, and a
transcript document above persistent microphone controls. Lavender identifies
primary actions; teal distinguishes file upload. Light and dark themes share
spacing, contrast and component states. Fonts and visuals are local assets.

Research references:

- [Linear's interface redesign](https://linear.app/now/how-we-redesigned-the-linear-ui): hierarchy, restrained navigation, and consistent surface contrast.
- [Riverside's editor](https://support.riverside.com/hc/en-us/articles/16673658517277-Riverside-editor-Overview): recording and transcript tools in one workspace.

These informed the layout; the implementation and waveform illustration are
original HTML/CSS, with no remote design assets or runtime CDN dependencies.

## Fixed workflows

- Microphone stop now sends a finish marker and waits for saved transcript
  acknowledgements before closing the WebSocket.
- Failed microphone setup and disconnected sockets release capture resources.
- Live-only sessions can close and export their text.
- Mixed sessions keep microphone text in the final export after the uploaded
  audio transcript. Those appended microphone blocks have no retained audio;
  their export timestamps use the end of the uploaded audio. Live-only timestamps
  represent cumulative captured utterance durations, excluding pauses.
- File processing updates appear automatically without replacing an active edit.
- A failed enqueue keeps the uploaded audio and exposes a retry action.
- Removing a clip also removes its transcript; removing a session removes all blocks.
- Clip reordering updates file-block order while retaining microphone positions.
- Duplicate upload runners are prevented; finalization waits for local capture
  and upload work to finish.
- Open WebSockets recheck account and session access for each utterance, reject
  mismatched browser origins, and reject malformed PCM instead of truncating it.

## What “live” means

Audio is captured locally, resampled to 16 kHz, and sent over the authenticated
WebSocket after a pause (or after a bounded continuous utterance). The local
Whisper model transcribes each utterance once. Text appears after inference;
this is not word-by-word streaming. Short phrases may wait to merge with another
phrase. CPU speed and concurrent work affect latency. The UI reports overload
rather than silently pretending a dropped chunk was saved.

Remote HTTP microphone capture still requires the browser's trusted-origin
exception. See [HTTP setup](ACCOUNTS_SETUP.md#recording-over-http-in-chrome).
No app setting can expose a microphone API withheld by the browser.

## Validation

All 92 automated tests pass. Automated coverage includes account approval, verification-code lifecycle,
password recovery, owner isolation, multi-file ordering, export formats,
cancellation, retries, live PCM, finalization and cleanup. Browser acceptance
covers microphone capture through AudioWorklet and the WebSocket to the installed
Whisper model, final-sentence acknowledgement, editing, upload completion,
finalization and text download, plus responsive layouts.

Bale requires network access to its service. Offline transcription and password
login do not require that connection. Automated Bale tests mock delivery; a real
Bale client is needed to verify that client's contact-sharing behavior.

## Operational repair

The deployed worker had exited because its Redis connection was read-only.
This installation now uses its own `voice-2-text-redis` container, bound only to
`127.0.0.1:6381`, with a persistent volume. Its private `.env` points to that port.
Compose installations continue to use their own internal `redis` service.
The existing unrelated Redis container was not modified. The worker and live
inference each use two CPU threads on this four-core host.
