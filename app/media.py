"""Audio probing. Uses PyAV (bundled with faster-whisper) and falls back to ffprobe."""
import json
import shutil
import subprocess
from pathlib import Path


def probe_duration(path: str | Path) -> float | None:
    """Duration in seconds, or None if it cannot be determined."""
    path = str(path)
    try:
        import av  # PyAV ships its own ffmpeg libs -- no system ffmpeg needed

        with av.open(path) as container:
            if container.duration is not None:
                return float(container.duration) / av.time_base
            for stream in container.streams.audio:
                if stream.duration and stream.time_base:
                    return float(stream.duration * stream.time_base)
    except Exception:
        pass

    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            out = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", path],
                capture_output=True, text=True, timeout=60, check=True,
            )
            value = json.loads(out.stdout)["format"]["duration"]
            return float(value)
        except Exception:
            pass
    return None


def has_audio_stream(path: str | Path) -> bool:
    try:
        import av

        with av.open(str(path)) as container:
            return len(container.streams.audio) > 0
    except Exception:
        # Can't prove it either way without a decoder; let the ASR stage decide.
        return True
