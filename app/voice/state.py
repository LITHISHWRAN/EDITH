"""
What EDITH is doing right now, as far as the microphone is concerned.

The microphone listens continuously, so something has to decide which audio
counts. Muting only covers the moment EDITH is speaking; it leaves the mic
live while a tool runs or the model thinks, and anything said or overheard
in that window arrives as the next command.

An explicit state covers all three phases:

    LISTENING  accept what is heard
    THINKING   ignore it -- a tool is running or the model is generating
    SPEAKING   ignore it -- the speakers are playing EDITH's own voice
"""

import threading
from contextlib import contextmanager
from enum import Enum

from app.observability.logging_setup import get_logger

log = get_logger("voice.state")


class VoiceState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class VoiceStateManager:
    """Thread-safe: the transcriber calls in from its own thread."""

    def __init__(self, state: VoiceState = VoiceState.IDLE):
        self._state = state
        self._lock = threading.Lock()
        self._observers = []

    # ------------------------------------------------------------------

    def observe(self, callback):
        """
        Called on every change, with the new state.

        Used to mute the microphone. Gating in the transcription callback is
        not enough on its own: it tests the state when the *transcript*
        arrives, which is a few hundred milliseconds after the audio was
        captured -- long enough for EDITH's own reply to be recorded during
        SPEAKING and then accepted because the state had already returned to
        LISTENING.
        """
        self._observers.append(callback)

    @property
    def state(self) -> VoiceState:
        with self._lock:
            return self._state

    def set(self, state: VoiceState):
        with self._lock:
            if state is self._state:
                return

            previous, self._state = self._state, state

        log.debug("%s -> %s", previous.value, state.value)

        for callback in self._observers:
            try:
                callback(state)

            except Exception:
                log.exception("state observer failed")

    def is_listening(self) -> bool:
        return self.state is VoiceState.LISTENING

    def is_speaking(self) -> bool:
        return self.state is VoiceState.SPEAKING

    # Convenience, so callers read as prose.
    def listening(self):
        self.set(VoiceState.LISTENING)

    def thinking(self):
        self.set(VoiceState.THINKING)

    def speaking(self):
        self.set(VoiceState.SPEAKING)

    def idle(self):
        self.set(VoiceState.IDLE)

    @contextmanager
    def busy(self, state: VoiceState):
        """
        Hold a state for the duration of a block, then return to listening.

        The restore runs even if the block raises -- a failed tool call must
        not leave EDITH permanently deaf.
        """
        self.set(state)

        try:
            yield

        finally:
            self.set(VoiceState.LISTENING)
