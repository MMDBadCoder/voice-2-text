"""The browser-side capture logic in app/static/live.js.

This file exists because of a real escape: `this.carry` was used for BOTH the
resampler's fractional index and the carried-over speech buffer, so the
resampler broke on the first audio frame and live capture never worked in a
browser at all. Every server-side test passed, because they fed PCM straight to
the WebSocket and skipped the browser entirely.
"""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

LIVE_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "live.js"
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node is not installed")


def run(body: str) -> dict:
    """Load live.js in node with a stubbed browser and run an assertion body."""
    harness = textwrap.dedent(f"""
        global.window = {{}};
        global.document = {{ getElementById: () => null }};
        global.navigator = {{}};
        global.WebSocket = function () {{}};
        global.WebSocket.OPEN = 1;
        const src = require('fs').readFileSync({str(LIVE_JS)!r}, 'utf8');
        eval(src);
        const LiveSession = global.window.LiveSession;
        const out = {{}};
        {body}
        console.log(JSON.stringify(out));
    """)
    proc = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _session(extra=""):
    return f"""
        const s = new LiveSession('job', {{
            state(){{}}, level(){{}}, sent(seq, ms){{ out.sent = (out.sent||0)+1; out.ms = ms; }},
            message(){{}},
        }});
        {extra}
    """


@pytest.mark.parametrize("rate", [48000, 44100, 16000])
def test_resampler_delivers_frames_to_the_detector(rate):
    """The bug: a broken resampler silently starved the detector, so nothing
    was ever sent and the UI just sat there looking connected."""
    res = run(_session(f"""
        s.ratio = {rate} / 16000;
        let frames = 0;
        s.analyse = () => frames++;
        for (let n = 0; n < 200; n++) s.ingest(new Float32Array(128).fill(0.1));
        out.frames = frames;
    """))
    assert res["frames"] > 0, f"no 16 kHz frames produced from {rate} Hz input"


def test_speech_then_pause_is_shipped():
    """Loud audio followed by a long pause must produce exactly one utterance."""
    res = run(_session("""
        s.ratio = 1;
        s.ws = { readyState: 1, send(){}, };
        const loud = () => { const f = new Float32Array(320); 
            for (let i=0;i<320;i++) f[i] = Math.sin(i/3) * 0.5; return f; };
        const quiet = () => new Float32Array(320);
        for (let n = 0; n < 250; n++) s.ingest(loud());    // 5s of speech
        for (let n = 0; n < 100; n++) s.ingest(quiet());   // 2s of silence
        out.sent = out.sent || 0;
    """))
    assert res["sent"] == 1, f"expected one utterance, got {res['sent']}"
    assert res["ms"] >= 2500


def test_short_speech_is_carried_not_dropped():
    """A brief word must survive: it rides along with the next utterance
    instead of being discarded for being under the minimum length."""
    res = run(_session("""
        s.ratio = 1;
        s.ws = { readyState: 1, send(){}, };
        const loud = () => { const f = new Float32Array(320);
            for (let i=0;i<320;i++) f[i] = Math.sin(i/3) * 0.5; return f; };
        const quiet = () => new Float32Array(320);
        for (let n = 0; n < 40; n++) s.ingest(loud());     // 0.8s -- under the minimum
        for (let n = 0; n < 60; n++) s.ingest(quiet());    // pause
        out.afterShort = out.sent || 0;
        for (let n = 0; n < 200; n++) s.ingest(loud());    // 4s -- a real utterance
        for (let n = 0; n < 100; n++) s.ingest(quiet());
        out.sent = out.sent || 0;
    """))
    assert res["afterShort"] == 0, "a sub-minimum fragment should not ship on its own yet"
    assert res["sent"] == 1
    # the carried 0.8s must be inside the shipped audio, not lost
    assert res["ms"] >= 4000 + 700, f"carried fragment missing (got {res['ms']}ms)"


def test_long_speech_is_capped_so_latency_stays_bounded():
    res = run(_session("""
        s.ratio = 1;
        s.ws = { readyState: 1, send(){}, };
        const loud = () => { const f = new Float32Array(320);
            for (let i=0;i<320;i++) f[i] = Math.sin(i/3) * 0.5; return f; };
        for (let n = 0; n < 1500; n++) s.ingest(loud());   // 30s with no pause at all
        out.sent = out.sent || 0;
    """))
    assert res["sent"] >= 1, "a speaker who never pauses must still get text"
    assert res["ms"] <= 20000
