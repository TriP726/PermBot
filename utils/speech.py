import io
import os
import asyncio
import importlib.util
from dataclasses import dataclass
from typing import Any, Optional

from utils.helpers import log, get_env

# Speech-to-text runs on this machine with faster-whisper (a Whisper port). It needs no
# API key and detects the spoken language itself. The model is downloaded once from
# Hugging Face, then kept in the user's cache.
DEFAULT_MODEL = "small"
SAMPLE_RATE = 16000
MAX_AUDIO_SECONDS = 300


class SpeechError(Exception):
    """Raised with a message that is safe to show to the user."""


@dataclass
class Transcript:
    text: str
    language: str
    duration: float


def _format_length(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}:{secs:02d}"


class SpeechTranscriber:
    """Lazy, single-file-at-a-time wrapper around a local Whisper model."""

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = (model_name or get_env("WHISPER_MODEL") or DEFAULT_MODEL).strip()
        self._model: Any = None
        self._downloading = False
        self._load_lock = asyncio.Lock()
        # Transcription is CPU heavy, so requests are handled one after another.
        self._run_lock = asyncio.Lock()

    @property
    def installed(self) -> bool:
        return importlib.util.find_spec("faster_whisper") is not None

    # ------------------------------------------------------------------
    # Blocking helpers (always run in a worker thread)
    # ------------------------------------------------------------------
    def _fetch_model_files(self) -> None:
        if os.path.isdir(self.model_name):
            return  # A local model folder was configured; nothing to download.

        from faster_whisper import download_model

        try:
            download_model(self.model_name, local_files_only=True)
            return
        except ValueError:
            raise  # Unknown model name.
        except Exception:
            pass  # Not cached yet.

        self._downloading = True
        try:
            log.info(f"Downloading speech model '{self.model_name}' for voice translation (one-time download)...")
            download_model(self.model_name)
            log.info(f"Speech model '{self.model_name}' downloaded.")
        finally:
            self._downloading = False

    def _load_model(self) -> Any:
        from faster_whisper import WhisperModel

        self._fetch_model_files()
        return WhisperModel(self.model_name, device="cpu", compute_type="int8")

    def _decode(self, data: bytes) -> Any:
        """Decode any audio container to mono 16 kHz float32 samples.

        Uses PyAV (installed with faster-whisper) directly instead of faster-whisper's own
        decoder, which passes an `av.open()` argument that newer PyAV releases removed.
        """
        import av
        import numpy as np

        resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        chunks = []
        with av.open(io.BytesIO(data), mode="r") as container:
            frames = container.decode(audio=0)
            while True:
                try:
                    frame = next(frames)
                except StopIteration:
                    break
                except av.error.InvalidDataError:
                    continue  # Skip a damaged packet instead of rejecting the whole file.
                chunks.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(frame))
            chunks.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(None))

        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32) / 32768.0

    def _run_model(self, model: Any, audio: Any) -> Transcript:
        # The VAD filter drops silence and noise, which Whisper otherwise tends to "hear" words in.
        segments, info = model.transcribe(audio, vad_filter=True, condition_on_previous_text=False)
        text = "".join(segment.text for segment in segments).strip()
        return Transcript(text=text, language=info.language or "", duration=len(audio) / SAMPLE_RATE)

    # ------------------------------------------------------------------
    # Async API
    # ------------------------------------------------------------------
    async def prefetch(self) -> None:
        """Download the model files ahead of time so the first voice message is not slow."""
        if not self.installed:
            return
        async with self._load_lock:
            try:
                await asyncio.to_thread(self._fetch_model_files)
            except Exception as e:
                log.warning(f"Could not download speech model '{self.model_name}' yet (will retry on first use): {e!r}")

    async def _get_model(self) -> Any:
        async with self._load_lock:
            if self._model is None:
                log.info(f"Loading speech model '{self.model_name}'...")
                try:
                    self._model = await asyncio.to_thread(self._load_model)
                except Exception as e:
                    log.error(f"Failed to load speech model '{self.model_name}': {e!r}")
                    raise SpeechError(
                        "The speech model could not be loaded. It has to be downloaded once, "
                        "so the bot may have been offline. Try again in a few minutes."
                    )
                log.info(f"Speech model '{self.model_name}' ready.")
            return self._model

    async def transcribe(self, data: bytes) -> Transcript:
        """Turn audio bytes (any format FFmpeg understands) into text."""
        if not self.installed:
            raise SpeechError(
                "Voice translation is not installed on this bot. "
                "Bot owner: run `python -m pip install -r requirements.txt` and restart."
            )
        if self._downloading:
            raise SpeechError("The speech model is still downloading (one-time setup). Try again in a few minutes.")

        try:
            audio = await asyncio.to_thread(self._decode, data)
        except Exception as e:
            log.warning(f"Could not decode audio for transcription: {e!r}")
            raise SpeechError("I could not read that audio file.")

        duration = len(audio) / SAMPLE_RATE
        if duration > MAX_AUDIO_SECONDS:
            raise SpeechError(
                f"That audio is {_format_length(duration)} long. "
                f"I can translate up to {_format_length(MAX_AUDIO_SECONDS)}."
            )
        if duration < 0.1:
            raise SpeechError("That audio is empty.")

        model = await self._get_model()
        async with self._run_lock:
            try:
                transcript = await asyncio.to_thread(self._run_model, model, audio)
            except Exception as e:
                log.error(f"Transcription failed: {e!r}")
                raise SpeechError("Something went wrong while transcribing that audio.")

        if not transcript.text:
            raise SpeechError("I could not make out any speech in that audio.")
        return transcript
