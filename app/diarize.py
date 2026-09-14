"""تفکیک گوینده — speaker diarization via sherpa-onnx.

sherpa-onnx over pyannote.audio here: the segmentation model is a ~7 MB ONNX
file, it runs on plain onnxruntime CPU, and it needs no HuggingFace token,
which matters on a locked-down offline box. Optional: DIARIZATION_ENABLED.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from . import config
from .asr import Segment

log = logging.getLogger(__name__)


@dataclass
class SpeakerTurn:
    start: float
    end: float
    speaker: str


def available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except ImportError:
        return False


def diarize(audio_path: str) -> list[SpeakerTurn]:
    """Return speaker turns. Empty list if diarization is off or unavailable."""
    if not config.DIARIZATION_ENABLED:
        return []
    if not available():
        log.warning("DIARIZATION_ENABLED but sherpa-onnx is not installed; skipping")
        return []

    import numpy as np
    import sherpa_onnx

    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=config.DIARIZATION_SEGMENTATION_MODEL
            ),
            num_threads=config.WORKER_CPU_THREADS,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=config.DIARIZATION_EMBEDDING_MODEL,
            num_threads=config.WORKER_CPU_THREADS,
        ),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=config.DIARIZATION_NUM_SPEAKERS or -1,
            threshold=config.DIARIZATION_CLUSTER_THRESHOLD,
        ),
    )
    if not cfg.validate():
        log.error("invalid sherpa-onnx diarization config; skipping")
        return []

    sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)

    import soundfile  # noqa: F401  (sherpa-onnx needs float32 mono at its sample rate)
    samples, sample_rate = _read_mono(audio_path, sd.sample_rate)
    result = sd.process(samples).sort_by_start_time()

    return [SpeakerTurn(start=r.start, end=r.end, speaker=f"گویندهٔ {r.speaker + 1}")
            for r in result]


def _read_mono(path: str, target_rate: int):
    """Decode any container to mono float32 at target_rate using PyAV."""
    import av
    import numpy as np

    resampler = av.AudioResampler(format="flt", layout="mono", rate=target_rate)
    chunks = []
    with av.open(path) as container:
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype="float32"), target_rate
    return np.concatenate(chunks).astype("float32"), target_rate


def assign_speakers(segments: list[Segment], turns: list[SpeakerTurn]) -> int:
    """Label each ASR segment with the speaker it overlaps most. Returns speaker count."""
    if not turns:
        return 0
    for seg in segments:
        best, best_overlap = None, 0.0
        for turn in turns:
            overlap = min(seg.end, turn.end) - max(seg.start, turn.start)
            if overlap > best_overlap:
                best, best_overlap = turn.speaker, overlap
        seg.speaker = best
    return len({t.speaker for t in turns})
