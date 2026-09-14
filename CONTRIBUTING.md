# Contributing

Use Linux and Python 3.11. No model download is necessary for the test suite.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
bash -n scripts/*.sh
node --check app/static/app.js    # optional syntax check if Node is installed
```

Tests use temporary SQLite storage and stub transcription. Tests that exercise
cancellation launch and terminate an owned child process; run them in an
environment that permits local process creation and FastAPI's threaded test
client. Redis operations are mocked for ordinary API tests.

Use the local setup guide for manual testing. Check narrow and wide viewports,
keyboard focus, file uploads, processing/stopping/error states, and downloads.
Keep Persian RTL layout and locally hosted assets intact. Do not add a runtime
CDN dependency to an offline application.

Keep changes focused and describe the problem, resulting behavior, and relevant
test results in pull requests. For database changes, preserve existing data and
add migration coverage. For cancellation changes, verify that the underlying
process exits—not just that its UI status changes.

Never commit `.env`, uploads, transcripts, model weights, logs, or credentials.
New code is contributed under the project's MIT license; third-party assets must
include their compatible license and attribution.
