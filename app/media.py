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


def concatenate_audio(paths: list[str], destination: str) -> float:
    """Stream ordered, mixed-format clips into one mono MP3 without buffering all audio."""
    import av
    from fractions import Fraction

    target = Path(destination)
    temporary = target.with_suffix('.assembling.mp3')
    samples_written = 0
    try:
        with av.open(str(temporary), 'w', format='mp3') as output:
            stream = output.add_stream('libmp3lame', rate=16000)
            stream.layout = 'mono'
            stream.bit_rate = 64000
            for path in paths:
                resampler = av.AudioResampler(format='fltp', layout='mono', rate=16000)
                def encode(frame):
                    nonlocal samples_written
                    frame.pts = samples_written
                    frame.time_base = Fraction(1,16000)
                    samples_written += frame.samples
                    for packet in stream.encode(frame):
                        output.mux(packet)
                with av.open(path) as source:
                    for frame in source.decode(audio=0):
                        for converted in resampler.resample(frame):
                            encode(converted)
                for converted in resampler.resample(None):
                    encode(converted)
            for packet in stream.encode(None):
                output.mux(packet)
        if not samples_written:
            raise ValueError('No audio samples in session')
        temporary.replace(target)
        return samples_written / 16000
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
