import importlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from crypto_utils import encrypt_token, generate_encryption_key


@pytest.fixture
def ingest(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", generate_encryption_key())
    monkeypatch.setenv("KENARI_API_KEY_ENCRYPTED", encrypt_token("test-token"))
    return importlib.import_module("daily_ingest")


def make_audio(path, duration):
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
        "anullsrc=r=16000:cl=mono", "-t", str(duration), "-b:a", "32k", str(path)
    ], check=True)


@pytest.mark.parametrize(
    ("duration", "expected_offsets"),
    [(2, [0]), (3, [0]), (6, [0, 3]), (7, [0, 3, 6])],
)
def test_transcription_splits_audio_and_preserves_timestamps(
    ingest, monkeypatch, tmp_path, duration, expected_offsets
):
    audio = tmp_path / "audio.mp3"
    make_audio(audio, duration)
    monkeypatch.setattr(ingest, "TRANSCRIPTION_PART_SEC", 3)
    files = []

    def create(**kwargs):
        files.append(Path(kwargs["file"].name))
        assert kwargs["file"].read()
        return SimpleNamespace(segments=[SimpleNamespace(text="kajian", start=0.1, end=0.5)])

    monkeypatch.setattr(ingest.kenari_client.audio.transcriptions, "create", create)
    segments = ingest.transcribe_audio(str(audio))

    assert [segment.start for segment in segments] == pytest.approx(
        [offset + 0.1 for offset in expected_offsets]
    )
    assert [segment.end for segment in segments] == pytest.approx(
        [offset + 0.5 for offset in expected_offsets]
    )
    if duration > 4:
        assert all(not path.exists() for path in files)
    else:
        assert files == [audio]


def test_transcription_failure_cleans_parts_and_propagates(ingest, monkeypatch, tmp_path):
    audio = tmp_path / "audio.mp3"
    make_audio(audio, 7)
    monkeypatch.setattr(ingest, "TRANSCRIPTION_PART_SEC", 3)
    files = []

    def create(**kwargs):
        files.append(Path(kwargs["file"].name))
        if len(files) == 2:
            return SimpleNamespace(segments=[])
        return SimpleNamespace(segments=[SimpleNamespace(text="kajian", start=0, end=1)])

    monkeypatch.setattr(ingest.kenari_client.audio.transcriptions, "create", create)
    with pytest.raises(RuntimeError, match="at 3s"):
        ingest.transcribe_audio(str(audio))
    assert all(not path.exists() for path in files)
