"""Microphone audio recording (with RMS silence auto-stop) and Groq Whisper STT."""

from __future__ import annotations

import io
import math
import threading
import time
import wave
from array import array
from typing import Any

import httpx

from app.ai import GROQ_BASE_URL

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # int16
MAX_RECORD_SECONDS = 120
MAX_PCM_BYTES = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH * MAX_RECORD_SECONDS
WHISPER_MODEL = "whisper-large-v3-turbo"
SPEECH_RMS_THRESHOLD = 450.0
SILENCE_RMS_THRESHOLD = 220.0
MIN_SPEECH_CHUNKS = 3


class VoiceError(Exception):
    """Raised when microphone recording or Whisper transcription fails."""


def _pcm_rms(chunk: bytes) -> float:
    usable = len(chunk) - (len(chunk) % 2)
    if usable <= 0:
        return 0.0
    samples = array("h")
    samples.frombytes(chunk[:usable])
    if not samples:
        return 0.0
    mean_sq = sum(s * s for s in samples) / len(samples)
    return math.sqrt(mean_sq)


def list_microphones() -> list[dict[str, Any]]:
    """Return available audio input devices safely."""
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        default_in = None
        try:
            default_in = sd.default.device[0]
        except Exception:
            default_in = None

        inputs: list[dict[str, Any]] = []
        for idx, dev in enumerate(devices):
            if int(dev.get("max_input_channels", 0)) > 0:
                inputs.append(
                    {
                        "id": idx,
                        "name": str(dev.get("name", f"Mic {idx}")),
                        "channels": int(dev.get("max_input_channels", 1)),
                        "is_default": idx == default_in,
                    }
                )
        return inputs
    except Exception:
        return []


class VoiceRecorder:
    """Thread-safe 16kHz mono PCM recorder producing standard WAV bytes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream: Any = None
        self._buffer = bytearray()
        self._recording = False
        self._started_at: float = 0.0
        self._auto_silence: bool = True
        self._silence_seconds: float = 1.8
        self._speech_detected: bool = False
        self._speech_chunks: int = 0
        self._last_speech_at: float = 0.0
        self._auto_stopped: bool = False

    def status(self) -> dict[str, Any]:
        with self._lock:
            elapsed = round(time.monotonic() - self._started_at, 1) if self._recording else 0.0
            return {
                "recording": self._recording,
                "elapsed_seconds": elapsed,
                "speech_detected": self._speech_detected,
                "auto_stopped": self._auto_stopped,
            }

    def start(
        self,
        device: int | str | None = None,
        auto_silence: bool = True,
        silence_seconds: float = 1.8,
    ) -> dict[str, Any]:
        with self._lock:
            if self._recording:
                raise VoiceError("Recording is already in progress.")
            try:
                import sounddevice as sd
            except Exception as exc:
                raise VoiceError(f"Audio library unavailable: {exc}") from exc

            self._buffer = bytearray()
            self._auto_silence = bool(auto_silence)
            self._silence_seconds = max(0.6, min(6.0, float(silence_seconds)))
            self._speech_detected = False
            self._speech_chunks = 0
            self._last_speech_at = time.monotonic()
            self._auto_stopped = False

            def _callback(indata: bytes, _frames: int, _time_info: Any, _status: Any) -> None:
                chunk = bytes(indata)
                if len(self._buffer) < MAX_PCM_BYTES:
                    remaining = MAX_PCM_BYTES - len(self._buffer)
                    self._buffer.extend(chunk[:remaining])
                if len(self._buffer) >= MAX_PCM_BYTES:
                    self._auto_stopped = True
                    raise sd.CallbackStop()

                if self._auto_silence:
                    rms = _pcm_rms(chunk)
                    now_t = time.monotonic()
                    if rms >= SPEECH_RMS_THRESHOLD:
                        self._speech_chunks += 1
                        self._last_speech_at = now_t
                        if self._speech_chunks >= MIN_SPEECH_CHUNKS:
                            self._speech_detected = True
                    elif (
                        self._speech_detected
                        and rms < SILENCE_RMS_THRESHOLD
                        and (now_t - self._last_speech_at) >= self._silence_seconds
                    ):
                        self._auto_stopped = True
                        raise sd.CallbackStop()

            dev_arg: int | str | None = device
            if isinstance(dev_arg, str):
                dev_arg = int(dev_arg) if dev_arg.isdigit() else (dev_arg.strip() or None)

            try:
                stream = sd.RawInputStream(
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    device=dev_arg,
                    callback=_callback,
                )
                stream.start()
            except Exception as exc:
                self._stream = None
                self._recording = False
                raise VoiceError(
                    f"Could not start microphone ({exc}). Check your Windows mic permissions or Settings."
                ) from exc

            self._stream = stream
            self._recording = True
            self._started_at = time.monotonic()
            return {
                "recording": True,
                "elapsed_seconds": 0.0,
                "speech_detected": False,
                "auto_stopped": False,
            }

    def stop(self) -> bytes:
        with self._lock:
            if not self._recording:
                raise VoiceError("No active voice recording.")
            stream = self._stream
            self._stream = None
            self._recording = False
            self._auto_stopped = False
            pcm_bytes = bytes(self._buffer)
            self._buffer = bytearray()

        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

        if len(pcm_bytes) < SAMPLE_RATE // 4:  # < ~0.125s of audio
            raise VoiceError("Recording was too short or silent.")

        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm_bytes)
        return wav_io.getvalue()

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            stream = self._stream
            self._stream = None
            self._recording = False
            self._auto_stopped = False
            self._speech_detected = False
            self._buffer = bytearray()
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        return {"recording": False, "cancelled": True}


recorder = VoiceRecorder()


async def transcribe_wav_groq(wav_bytes: bytes, api_key: str) -> str:
    """Send WAV audio bytes to Groq Whisper and return transcribed text."""
    key = (api_key or "").strip()
    if not key:
        raise VoiceError("Groq API key is required for voice transcription. Add it in Settings.")
    if not wav_bytes:
        raise VoiceError("Empty audio buffer.")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{GROQ_BASE_URL}/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            files={"file": ("voice.wav", wav_bytes, "audio/wav")},
            data={"model": WHISPER_MODEL, "response_format": "json"},
        )
    if resp.status_code == 401:
        raise VoiceError("Invalid Groq API key (401 Unauthorized).")
    if resp.status_code >= 400:
        raise VoiceError(f"Groq Whisper error ({resp.status_code}): {resp.text[:220]}")
    payload = resp.json()
    text = str(payload.get("text") or "").strip()
    if not text:
        raise VoiceError("No speech was detected in the recording.")
    return text
