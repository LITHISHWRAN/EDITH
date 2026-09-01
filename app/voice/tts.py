"""
Speech output, using Kokoro.

Measured on this machine (CPU, ONNX):
    realtime factor      0.33
    short reply          ~470 ms
    long reply, whole    4705 ms to first sound
    long reply, per-sentence  1993 ms to first sound, no playback gaps

So speech is generated one sentence at a time and queued for playback:
generation stays comfortably ahead of the speaker, and the user hears the
first words while the rest is still being made.
"""

import queue
import re
import threading

from app.config import CONFIG
from app.observability.logging_setup import get_logger

log = get_logger("voice.tts")

# Split on sentence ends, then on clauses if a sentence is long enough that
# waiting for all of it would be audible.
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_CLAUSE = re.compile(r"(?<=[,;:])\s+")

LONG_SENTENCE_CHARS = 120

# Markdown reads badly aloud: "star star Chrome star star".
_STRIP_MARKDOWN = [
    (re.compile(r"```.*?```", re.S), " code block "),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\*\*([^*]*)\*\*"), r"\1"),
    (re.compile(r"\*([^*]*)\*"), r"\1"),
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),
    (re.compile(r"^\s*[-*]\s+", re.M), ""),
    (re.compile(r"#+\s*"), ""),
]


def speakable(text: str) -> str:
    """Strip formatting that is meaningless out loud."""
    cleaned = text or ""

    for pattern, replacement in _STRIP_MARKDOWN:
        cleaned = pattern.sub(replacement, cleaned)

    return " ".join(cleaned.split())


def split_for_speech(text: str) -> list[str]:
    """Chunks small enough that the first one is quick to generate."""
    chunks = []

    for sentence in _SENTENCE.split(speakable(text)):
        sentence = sentence.strip()

        if not sentence:
            continue

        if len(sentence) <= LONG_SENTENCE_CHARS:
            chunks.append(sentence)
            continue

        # Long sentence: break at clauses so speech starts sooner.
        buffer = ""

        for clause in _CLAUSE.split(sentence):
            if len(buffer) + len(clause) > LONG_SENTENCE_CHARS and buffer:
                chunks.append(buffer.strip())
                buffer = clause
            else:
                buffer = f"{buffer} {clause}".strip()

        if buffer:
            chunks.append(buffer.strip())

    return chunks


class Speaker:
    """
    Turns text into audio and plays it, one chunk ahead of the speaker.

    Loading is lazy: importing the model costs a second, and a text-only
    session should not pay it.
    """

    def __init__(self):
        self._kokoro = None
        self._queue: queue.Queue = queue.Queue()
        self._player: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------

    def _model(self):
        with self._lock:
            if self._kokoro is not None:
                return self._kokoro

            from kokoro_onnx import Kokoro

            voice = CONFIG.voice

            if not voice.model_path.exists() or not voice.voices_path.exists():
                raise FileNotFoundError(
                    f"Kokoro model files are missing from {voice.model_path.parent}. "
                    f"Run scripts/download_voice_models.py."
                )

            log.info("loading Kokoro from %s", voice.model_path)

            self._kokoro = Kokoro(
                str(voice.model_path),
                str(voice.voices_path),
            )

            return self._kokoro

    def warm(self):
        """Pay the load and first-inference cost before the user is waiting."""
        try:
            self._model().create("ready", voice=CONFIG.voice.voice, lang="en-us")
            log.info("TTS warm")

        except Exception:
            log.exception("TTS warm-up failed")

    # ------------------------------------------------------------------

    def say(self, text: str, blocking: bool = True):
        chunks = split_for_speech(text)

        if not chunks:
            return

        self._ensure_player()
        self._stop.clear()

        voice = CONFIG.voice

        for chunk in chunks:
            if self._stop.is_set():
                break

            try:
                samples, rate = self._model().create(
                    chunk,
                    voice=voice.voice,
                    speed=voice.speed,
                    lang=voice.language,
                )

            except Exception:
                log.exception("TTS failed for chunk %r", chunk[:60])
                continue

            self._queue.put((samples, rate))

        self._queue.put(None)  # end of utterance

        if blocking:
            self._queue.join()

    def stop(self):
        """Cut playback short, for barge-in."""
        self._stop.set()

        try:
            while True:
                self._queue.get_nowait()
                self._queue.task_done()

        except queue.Empty:
            pass

    # ------------------------------------------------------------------

    def _ensure_player(self):
        if self._player is not None and self._player.is_alive():
            return

        self._player = threading.Thread(target=self._play_loop, daemon=True)
        self._player.start()

    def _play_loop(self):
        import sounddevice as sd

        while True:
            item = self._queue.get()

            if item is None:
                self._queue.task_done()
                continue

            samples, rate = item

            try:
                if not self._stop.is_set():
                    sd.play(samples, rate)
                    sd.wait()

            except Exception:
                log.exception("audio playback failed")

            finally:
                self._queue.task_done()
