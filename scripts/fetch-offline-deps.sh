#!/usr/bin/env bash
# ============================================================================
#  Build the offline transfer bundle. RUN THIS ON AN INTERNET-CONNECTED MACHINE.
#
#  Produces ./bundle/, which you copy to the offline box (USB, scp, whatever).
#  Nothing here runs on the offline box -- torch and transformers, the two big
#  dependencies, are only ever installed here, to convert the models.
#
#    ./scripts/fetch-offline-deps.sh                      # prebuilt CT2 model, no torch (~1.8 GB)
#    ./scripts/fetch-offline-deps.sh --convert            # build the fast turbo tier (needs torch)
#    ./scripts/fetch-offline-deps.sh --convert --accurate --diarization --images
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLE="${BUNDLE_DIR:-$ROOT/bundle}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
PYTHON_BIN="${PYTHON_BIN:-}"

WITH_ACCURATE=0
WITH_DIARIZATION=0
WITH_IMAGES=0
WITH_FONTS=1

# Two ways to get a model:
#   1. PREBUILT (default, no torch): download a repo that is ALREADY in
#      CTranslate2 int8 format. Costs one 1.56 GB download and nothing else.
#   2. CONVERT (--convert): pull an fp32/fp16 HuggingFace checkpoint and run
#      ct2-transformers-converter on it. Needs torch+transformers (~3 GB) here.
#      Required for turbo-based Persian models, which nobody has published in
#      CT2 form yet.
MODEL_ACCURATE_PREBUILT="${MODEL_ACCURATE_PREBUILT:-AmirMohseni/whisper-large-v3-persian-ct2-int8}"
MODEL_FAST_HF="${MODEL_FAST_HF:-vhdm/whisper-large-fa-v1}"
MODEL_ACCURATE_HF="${MODEL_ACCURATE_HF:-MohammadGholizadeh/whisper-large-v3-persian-common-voice-17}"
CONVERT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --accurate)     WITH_ACCURATE=1 ;;
    --convert)      CONVERT=1 ;;
    --diarization)  WITH_DIARIZATION=1 ;;
    --images)       WITH_IMAGES=1 ;;
    --no-fonts)     WITH_FONTS=0 ;;
    --python)       PYTHON_VERSION="$2"; shift ;;
    -h|--help)      sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

mkdir -p "$BUNDLE"/{wheels,models,fonts,images}

# ---------------------------------------------------------------- 1. wheels --
say "Preparing wheels for Python $PYTHON_VERSION on this platform"
PYTHON_BIN="${PYTHON_BIN:-python$PYTHON_VERSION}"
command -v "$PYTHON_BIN" >/dev/null || die "$PYTHON_BIN not found; install the target Python version first"
ACTUAL_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
[[ "$ACTUAL_VERSION" == "$PYTHON_VERSION" ]] || die "Python version mismatch: expected $PYTHON_VERSION, got $ACTUAL_VERSION"
# Prepare on the same OS/architecture/Python as the target. Resolving with one
# interpreter and downloading for another can create an unusable offline lock.
TMP_VENV="$(mktemp -d)"
trap 'rm -rf "$TMP_VENV"' EXIT
"$PYTHON_BIN" -m venv "$TMP_VENV"
"$TMP_VENV/bin/python" -m pip install -q --upgrade pip
"$TMP_VENV/bin/python" -m pip install -q --only-binary=:all: -r "$ROOT/requirements.txt"
[[ $WITH_DIARIZATION -eq 1 ]] && "$TMP_VENV/bin/python" -m pip install -q sherpa-onnx soundfile
"$TMP_VENV/bin/python" -m pip freeze > "$BUNDLE/requirements.lock.txt"
"$TMP_VENV/bin/python" -m pip download -q --only-binary=:all: \
    -r "$BUNDLE/requirements.lock.txt" -d "$BUNDLE/wheels" || die "A required wheel is unavailable"
echo "    $(ls "$BUNDLE/wheels" | wc -l) wheels, $(du -sh "$BUNDLE/wheels" | cut -f1)"

# ---------------------------------------------------------------- 2. models --
download_prebuilt() {
  # Fetch a repo that is already CTranslate2 int8. No torch, no conversion.
  local hf_id="$1" out="$2"
  if [[ -f "$out/model.bin" && -f "$out/tokenizer.json" ]]; then echo "    $out exists, skipping"; return; fi
  mkdir -p "$out"
  echo "    $hf_id  ->  $out"
  local base="https://huggingface.co/$hf_id/resolve/main"
  # model.bin and config.json are mandatory; the rest depends on the repo.
  for f in config.json model.bin; do
    curl -fsSL --retry 3 "$base/$f" -o "$out/$f" \
      || die "could not fetch $f from $hf_id"
  done
  for f in tokenizer.json tokenizer_config.json vocabulary.json vocabulary.txt \
           vocab.json merges.txt preprocessor_config.json; do
    curl -fsSL --retry 2 "$base/$f" -o "$out/$f" 2>/dev/null || rm -f "$out/$f"
  done
  [[ -f "$out/tokenizer.json" ]] || warn "$out has no tokenizer.json -- faster-whisper may refuse to load it"
  echo "    $(du -sh "$out" | cut -f1)"
}

convert_model() {
  local hf_id="$1" out="$2"
  if [[ -f "$out/model.bin" && -f "$out/tokenizer.json" ]]; then echo "    $out exists, skipping"; return; fi
  "$PYTHON_BIN" -c 'import ctranslate2, transformers, torch' 2>/dev/null \
    || die "Install requirements-converter.txt in your Python environment before --convert (see docs/SETUP.md)"
  echo "    converting $hf_id  ->  $out"
  "$PYTHON_BIN" "$ROOT/scripts/convert-model.py" "$hf_id" "$out"

  echo "    $(du -sh "$out" | cut -f1)"
}

if [[ $CONVERT -eq 1 ]]; then
  say "Converting HuggingFace checkpoints to CTranslate2 int8 (needs torch)"
  convert_model "$MODEL_FAST_HF" "$BUNDLE/models/whisper-fa-turbo-int8"
  [[ $WITH_ACCURATE -eq 1 ]] && convert_model "$MODEL_ACCURATE_HF" "$BUNDLE/models/whisper-fa-large-v3-int8"
else
  say "Downloading prebuilt CTranslate2 int8 model (no torch needed)"
  download_prebuilt "$MODEL_ACCURATE_PREBUILT" "$BUNDLE/models/whisper-fa-large-v3-int8"
  echo
  echo "    NOTE: this is the large-v3 'accurate' tier. Set ENABLED_TIERS=accurate"
  echo "    and DEFAULT_TIER=accurate in .env, or re-run with --convert to also"
  echo "    build the faster turbo tier from $MODEL_FAST_HF."
fi

# Silero VAD ships inside the faster-whisper wheel -- nothing to fetch here.

# ----------------------------------------------------------- 3. diarization --
if [[ $WITH_DIARIZATION -eq 1 ]]; then
  say "Downloading sherpa-onnx diarization models (~47 MB)"
  mkdir -p "$BUNDLE/models/diarization"
  SEG_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
  EMB_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
  tmp="$(mktemp -d)"
  curl -fsSL "$SEG_URL" -o "$tmp/seg.tar.bz2" && tar -xjf "$tmp/seg.tar.bz2" -C "$tmp"
  find "$tmp" -name "model.onnx" -exec cp {} "$BUNDLE/models/diarization/segmentation.onnx" \;
  curl -fsSL "$EMB_URL" -o "$BUNDLE/models/diarization/embedding.onnx"
  rm -rf "$tmp"
  echo "    $(du -sh "$BUNDLE/models/diarization" | cut -f1)"
fi

# ----------------------------------------------------------------- 4. fonts --
if [[ $WITH_FONTS -eq 1 ]]; then
  say "Downloading Vazirmatn (the offline box cannot reach Google Fonts)"
  FONT_URL="https://github.com/rastikerdar/vazirmatn/releases/download/v33.003/vazirmatn-v33.003.zip"
  tmp="$(mktemp -d)"
  if curl -fsSL "$FONT_URL" -o "$tmp/v.zip" 2>/dev/null; then
    unzip -qo "$tmp/v.zip" -d "$tmp"
    find "$tmp" -name "Vazirmatn-Regular.woff2" -exec cp {} "$BUNDLE/fonts/" \;
    find "$tmp" -name "Vazirmatn-Bold.woff2"    -exec cp {} "$BUNDLE/fonts/" \;
    cp "$ROOT/app/static/fonts/OFL.txt" "$BUNDLE/fonts/"
    echo "    $(ls "$BUNDLE/fonts" | wc -l) font file(s)"
  else
    warn "font download failed; the UI falls back to Tahoma/Noto Naskh Arabic"
  fi
  rm -rf "$tmp"
fi

# ---------------------------------------------------------------- 5. images --
if [[ $WITH_IMAGES -eq 1 ]]; then
  say "Saving docker images"
  command -v docker >/dev/null || die "docker not found"
  docker pull -q redis:7-alpine && docker save redis:7-alpine -o "$BUNDLE/images/redis-7-alpine.tar"
  docker pull -q python:3.11-slim && docker save python:3.11-slim -o "$BUNDLE/images/python-3.11-slim.tar"
  echo "    $(du -sh "$BUNDLE/images" | cut -f1)"
fi

# ----------------------------------------------------------------- summary --
cp "$ROOT/scripts/install-offline.sh" "$BUNDLE/" 2>/dev/null || true

say "Bundle ready: $BUNDLE"
du -sh "$BUNDLE"/* 2>/dev/null | sed 's/^/    /'
printf '\n    \033[1mTOTAL: %s\033[0m\n\n' "$(du -sh "$BUNDLE" | cut -f1)"
echo "Copy $BUNDLE to the offline machine, then run install-offline.sh there."
