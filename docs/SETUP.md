# Setup

## 1. Get the project and prerequisites

Use Linux and Python 3.11. On a Debian/Ubuntu system, install Redis, `curl`,
`unzip`, and a Python 3.11 interpreter with its `venv` module using your OS package
manager. Package names and Python availability vary by distribution.
Docker users can skip the host Redis/Python runtime and use
[Deployment](DEPLOYMENT.md); Python is still needed to prepare models with the
bundle script, unless you copy an existing CTranslate2 model directory.

```bash
git clone https://github.com/MMDBadCoder/voice-2-text.git
cd voice-2-text
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

The app does not automatically load `.env` on import. `scripts/run-local.sh`
loads it for local runs; Docker Compose injects it into containers. When running
Python commands directly, export configuration first:

```bash
set -a
source .env
set +a
```

Do not commit `.env`. Values with spaces must be quoted because the local launcher
sources this file as shell configuration.

## 2. Try the interface without a model

Start Redis. For a systemd installation:

```bash
sudo systemctl enable --now redis-server
redis-cli ping
```

Edit `.env`:

```dotenv
ASR_BACKEND=stub
REDIS_URL=redis://127.0.0.1:6379/0
WORKER_COUNT=1
WORKER_CPU_THREADS=2
RESERVED_CORES=1
```

Run:

```bash
./scripts/run-local.sh
```

Visit http://127.0.0.1:8000 and upload an audio file. The banner identifies this as
a demo: its output is fixed, fabricated Persian sentences unrelated to the audio.
Keep the terminal open; Ctrl+C shuts down the launched API and workers.

## 3. Download the accurate model and an offline dependency bundle

On an internet-connected Linux machine with Python 3.11:

```bash
./scripts/fetch-offline-deps.sh
```

The script resolves dependencies under Python 3.11, saves a lock file and binary
wheels, and downloads the preconverted model from
[AmirMohseni/whisper-large-v3-persian-ct2-int8](https://huggingface.co/AmirMohseni/whisper-large-v3-persian-ct2-int8).
Allow several GB of temporary disk space. Model weights are not in this Git repo.
Preparation can take time depending on bandwidth.

Copy the model into the local installation:

```bash
mkdir -p models
cp -a bundle/models/. models/
```

Set `.env`:

```dotenv
ASR_BACKEND=faster_whisper
ENABLED_TIERS=accurate
DEFAULT_TIER=accurate
MODEL_ACCURATE=./models/whisper-fa-large-v3-int8
```

Restart `./scripts/run-local.sh`. A CTranslate2 directory must include `model.bin`,
`config.json` and the required tokenizer/vocabulary files. Do not point the
runtime at an unconverted PyTorch checkpoint.

## 4. Optional fast tier

The fast tier uses [vhdm/whisper-large-fa-v1](https://huggingface.co/vhdm/whisper-large-fa-v1).
It requires conversion on the connected preparation machine. It can be less
accurate on conversational Persian; compare both tiers with your own recordings.

Keep converter dependencies separate from the app environment:

```bash
python3.11 -m venv .venv-converter
.venv-converter/bin/python -m pip install --upgrade pip
.venv-converter/bin/python -m pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
.venv-converter/bin/python -m pip install -r requirements-converter.txt
PYTHON_BIN="$PWD/.venv-converter/bin/python" ./scripts/fetch-offline-deps.sh --convert
cp -a bundle/models/. models/
```

The converter generates `tokenizer.json` for checkpoints that only provide a
legacy vocabulary. To convert a different compatible checkpoint directly:

```bash
.venv-converter/bin/python scripts/convert-model.py MODEL_ID models/my-model
```

After both models are installed:

```dotenv
ENABLED_TIERS=fast,accurate
DEFAULT_TIER=accurate
MODEL_FAST=./models/whisper-fa-turbo-int8
```

`--convert --accurate` also converts a full-model checkpoint; it is unnecessary if
you already downloaded the accurate prebuilt model. Review the model providers'
licenses before redistributing weights.

## Optional speaker diarization

Prepare the extra models and dependencies:

```bash
./scripts/fetch-offline-deps.sh --diarization
.venv/bin/python -m pip install sherpa-onnx soundfile
cp -a bundle/models/. models/
```

Set `DIARIZATION_ENABLED=true` and restart. For Docker, use an offline image built
from this bundle, or add these dependencies to your own image. Diarization is
optional and has not been validated across all recordings/models; a diarization
error keeps the transcription and logs the failure.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Repeated text unrelated to the recording | Switch `ASR_BACKEND=stub` to `faster_whisper`; restart both roles. |
| Job stays queued | Redis reachable? Worker running? Inspect worker logs and `/api/health`. |
| CPU budget error | Lower worker count/threads or reserved cores; see [Configuration](CONFIGURATION.md). |
| Model not found | Confirm directory names, enabled tiers, and `model.bin`. |
| Missing `tokenizer.json` | Use `scripts/convert-model.py`; do not copy only `model.bin`. |
| `No matching distribution` offline | Prepare wheels for the same Python version, Linux distribution and CPU architecture. |
| Permission denied under `data/` in Docker | Set directory ownership to container UID 10001; see deployment guide. |
| API works but worker is unreachable | Docker uses service hostname `redis`; local runs use `127.0.0.1`. |
| First progress update takes time | Loading the model, VAD and the first segment precede measurable progress. |
| Cancel shows “stopping” | Wait for the supervisor to confirm child exit; inspect worker logs if it persists. |
