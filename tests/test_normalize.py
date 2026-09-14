from app.normalize import ZWNJ, collapse_repetitions, is_hallucinated, normalize


def test_arabic_letterforms_become_persian():
    # This is the single most common defect in raw Whisper Persian output.
    assert normalize("يك كتاب") == "یک کتاب"
    assert "ي" not in normalize("مي‌روم")
    assert "ك" not in normalize("كار")


def test_diacritics_stripped():
    assert normalize("مُحَمَّد") == "محمد"


def test_zwnj_applied_to_prefixes_and_suffixes():
    assert normalize("می روم") == f"می{ZWNJ}روم"
    assert normalize("کتاب ها") == f"کتاب{ZWNJ}ها"
    assert normalize("بزرگ تر") == f"بزرگ{ZWNJ}تر"


def test_stray_zwnj_removed():
    assert normalize(f"سلام{ZWNJ} دنیا") == "سلام دنیا"


def test_digit_conversion_modes():
    assert normalize("سال 1403", digits="persian") == "سال ۱۴۰۳"
    assert normalize("سال ۱۴۰۳", digits="latin") == "سال 1403"
    assert normalize("سال ١٤٠٣", digits="latin") == "سال 1403"   # Arabic-Indic
    assert normalize("سال 1403", digits="keep") == "سال 1403"


def test_punctuation_spacing():
    assert normalize("سلام ، خوبی ؟") == "سلام، خوبی؟"
    assert normalize("بله،خوبم") == "بله، خوبم"


def test_latin_punctuation_becomes_persian():
    assert normalize("چطوری?") == "چطوری؟"


def test_repetition_loop_collapsed():
    # Whisper's signature hallucination when condition_on_previous_text leaks.
    assert collapse_repetitions("بله بله بله بله بله") == "بله"


def test_hallucination_detector():
    assert is_hallucinated("خب خب خب خب خب خب")
    assert not is_hallucinated("جلسه را شروع می‌کنیم و گزارش را می‌خوانیم")
    assert not is_hallucinated("بله")  # too short to judge


def test_normalize_is_idempotent():
    once = normalize("مي‌خواهم كتاب ها را بخوانم 5 عدد")
    assert normalize(once) == once


def test_empty_input():
    assert normalize("") == ""
    assert normalize(None) == ""


def test_stored_segments_are_normalized(monkeypatch, tmp_path):
    """The UI renders stored segments while downloads re-normalize. If the two
    disagree, users see `می خوام` on screen and `می‌خوام` in the file."""
    import sys
    sys.path.insert(0, str(tmp_path.parent))
    from app import asr, exporters
    from app.normalize import ZWNJ

    seg = asr.Segment(start=0.0, end=5.0, text="من مي خوام كتاب ها را بخوانم")
    res = asr.TranscriptionResult(segments=[seg], language="fa", duration=5.0)

    # What the exporter writes...
    exported = exporters.to_plain(res)
    # ...must be what a normalized stored segment already contains.
    from app.normalize import normalize
    assert normalize(seg.text) == exported
    assert "ي" not in exported and "ك" not in exported
    assert f"می{ZWNJ}خوام" in exported
