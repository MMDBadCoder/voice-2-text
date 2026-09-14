"""خروجی‌ها — TXT, SRT, VTT, JSON, DOCX (همه راست‌به‌چپ)."""
from __future__ import annotations

import json
from pathlib import Path

from .asr import TranscriptionResult
from .normalize import normalize

RLM = "‏"  # RIGHT-TO-LEFT MARK, keeps punctuation on the correct side


def _ts_srt(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ts_vtt(seconds: float) -> str:
    return _ts_srt(seconds).replace(",", ".")


def _ts_human(seconds: float) -> str:
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _line(seg, *, with_speaker: bool) -> str:
    text = normalize(seg.text)
    if with_speaker and seg.speaker:
        return f"{seg.speaker}: {text}"
    return text


def to_txt(result: TranscriptionResult, *, timestamps: bool = True) -> str:
    has_speakers = any(s.speaker for s in result.segments)
    lines = []
    current = None
    for seg in result.segments:
        if has_speakers and seg.speaker != current:
            current = seg.speaker
            lines.append("")
            lines.append(f"{RLM}— {seg.speaker or 'نامشخص'}")
        prefix = f"{RLM}[{_ts_human(seg.start)}] " if timestamps else RLM
        lines.append(prefix + normalize(seg.text))
    return "\n".join(lines).strip() + "\n"


def to_plain(result: TranscriptionResult) -> str:
    """One flowing paragraph -- what people paste into a report."""
    return normalize(" ".join(s.text for s in result.segments))


def to_srt(result: TranscriptionResult) -> str:
    out = []
    for i, seg in enumerate(result.segments, 1):
        out.append(str(i))
        out.append(f"{_ts_srt(seg.start)} --> {_ts_srt(seg.end)}")
        out.append(RLM + _line(seg, with_speaker=True))
        out.append("")
    return "\n".join(out)


def to_vtt(result: TranscriptionResult) -> str:
    out = ["WEBVTT", ""]
    for seg in result.segments:
        out.append(f"{_ts_vtt(seg.start)} --> {_ts_vtt(seg.end)}")
        out.append(RLM + _line(seg, with_speaker=True))
        out.append("")
    return "\n".join(out)


def to_json(result: TranscriptionResult, meta: dict | None = None) -> str:
    payload = {
        "meta": meta or {},
        "language": result.language,
        "language_probability": round(result.language_probability, 4),
        "duration": round(result.duration, 3),
        "backend": result.backend,
        "model": result.model,
        "text": to_plain(result),
        "segments": [s.to_dict() for s in result.segments],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def to_docx(result: TranscriptionResult, path: Path, *, title: str, meta: dict | None = None) -> bool:
    """Write an RTL .docx. Returns False if python-docx is not installed."""
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
        from docx.shared import Pt
    except ImportError:
        return False

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Vazirmatn"
    style.font.size = Pt(11)
    # python-docx does not set the complex-script font; Word ignores RTL without it.
    style.element.rPr.rFonts.set(qn("w:cs"), "Vazirmatn")

    def rtl(par):
        par.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        pPr = par._p.get_or_add_pPr()
        bidi = pPr.makeelement(qn("w:bidi"), {})
        pPr.append(bidi)
        return par

    rtl(doc.add_heading(title, level=1))
    if meta:
        info = "  |  ".join(f"{k}: {v}" for k, v in meta.items() if v not in (None, ""))
        if info:
            rtl(doc.add_paragraph(info)).runs[0].italic = True
    doc.add_paragraph()

    has_speakers = any(s.speaker for s in result.segments)
    current = None
    for seg in result.segments:
        if has_speakers and seg.speaker != current:
            current = seg.speaker
            p = rtl(doc.add_paragraph())
            run = p.add_run(seg.speaker or "نامشخص")
            run.bold = True
        p = rtl(doc.add_paragraph())
        p.add_run(f"[{_ts_human(seg.start)}] ").italic = True
        p.add_run(normalize(seg.text))

    doc.save(str(path))
    return True


FORMATS = ("txt", "plain", "srt", "vtt", "json", "docx")
MEDIA_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "plain": "text/plain; charset=utf-8",
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
