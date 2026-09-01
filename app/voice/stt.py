"""
Speech input, using Moonshine's streaming microphone transcriber.

Replaces a hand-rolled capture loop that decided when speech started by
comparing block energy to a calibrated noise floor. That kept losing the
start of commands -- "open chrome brave" came back as "Now watch your chrome
braid" -- because a word's opening consonant is quieter than the threshold
meant to detect it. MicTranscriber does capture, endpointing and streaming
transcription together.

Two things make it usable as an assistant rather than a dictation box:

  state gating   audio is only accepted while EDITH is LISTENING, so it
                 never transcribes its own reply or whatever was said while
                 a tool was running
  keyterms       the decoder is biased towards names actually installed on
                 this machine, which is what turns "chrome braid" into
                 "chrome brave"
"""

import queue
import threading
import time

from app.config import CONFIG
from app.observability.logging_setup import get_logger
from app.voice.state import VoiceStateManager

log = get_logger("voice.stt")

# The decoder is nudged towards these. Kept modest: a long list dilutes the
# bias and slows decoding.
MAX_KEYTERMS = 60

# Command words worth biasing even though no catalog contains them.
COMMAND_KEYTERMS = [
    "open", "close", "play", "search", "read", "delete", "move", "copy",
    "rename", "duplicate", "folder", "downloads", "documents", "desktop",
    "pictures", "videos", "music", "recycle bin", "clipboard", "exit",
]


def _model_arch():
    from moonshine_voice import ModelArch

    name = CONFIG.voice.stt_model.upper().replace("-", "_")

    return getattr(ModelArch, name, ModelArch.SMALL_STREAMING)


class MoonshineSTT:

    def __init__(self, state: VoiceStateManager, apps_index=None):
        self.state = state
        self.apps = apps_index

        # A queue rather than one slot guarded by an Event. With a single
        # slot, a line arriving while the consumer was reading was lost
        # twice over: its text overwrote the unread one, and the consumer's
        # clear() then destroyed the wake-up that announced it.
        self._lines: queue.Queue = queue.Queue()

        self.lock = threading.Lock()
        self.mic = None
        self._on_partial = None
        self._running = False

        # Follow the state so the microphone is deaf at the source, not
        # merely ignored after the fact.
        state.observe(self._on_state_change)

    # ==================================================================
    # Transcription callback
    # ==================================================================

    def _on_line(self, line):
        # Only what is said while EDITH is listening counts.
        #
        #   THINKING  a tool is running -- ignore
        #   SPEAKING  the speakers are playing EDITH's voice -- ignore
        #   LISTENING accept
        if not self.state.is_listening():
            return

        # A partial line would fire a command mid-sentence.
        if not getattr(line, "is_complete", True):
            return

        text = (getattr(line, "text", "") or "").strip()

        if not text:
            return

        latency = getattr(line, "last_transcription_latency_ms", None)
        log.info("heard %r%s", text, f" ({latency:.0f} ms)" if latency else "")

        self._lines.put(text)

    def _on_state_change(self, state):
        """
        Mute whenever EDITH is not listening.

        This is the guard that actually works. The callback check below runs
        when a transcript is *delivered*; muting stops the audio ever being
        recorded, which is the only way to keep EDITH's own voice out of the
        decoder.
        """
        from app.voice.state import VoiceState

        self.mute(state is not VoiceState.LISTENING)

    def mute(self, muted: bool = True):
        if self.mic is None:
            return

        try:
            self.mic.mute(muted)
            log.debug("microphone %s", "muted" if muted else "live")

        except Exception:
            log.exception("could not change the microphone mute state")

    def _on_text(self, text):
        if self._on_partial and self.state.is_listening():
            try:
                self._on_partial(text or "")

            except Exception:
                log.exception("partial-text callback failed")

    # ==================================================================
    # Start
    # ==================================================================

    def start(self, on_partial=None):
        from moonshine_voice import MicTranscriber

        if self._running:
            return

        self._on_partial = on_partial

        log.info("loading moonshine %s", CONFIG.voice.stt_model)

        self.mic = (
            MicTranscriber()
            .language(CONFIG.voice.stt_language)
            .model_arch(_model_arch())
            .update_interval(CONFIG.voice.update_interval)
            .on_line(self._on_line)
            .on_text(self._on_text)
            .on_error(lambda error: log.error("microphone error: %s", error))
        )

        self.mic.load()
        self._apply_keyterms()
        self.mic.start()

        self._running = True

        log.info("microphone listening")

    # ==================================================================
    # Wait for a sentence
    # ==================================================================

    def wait_for_sentence(self, timeout: float | None = None) -> str | None:
        """
        Block until the user has finished speaking, then return what was
        said as one command.

        The transcriber ends a *line* whenever it hears a pause, so a
        moment's hesitation splits an utterance: "open... chrome" arrives as
        two lines and would run as two commands, neither of which makes
        sense. Lines are therefore joined until the microphone has been
        quiet for a beat.

        A timeout is supported so the caller keeps control of the loop and
        can still respond to Ctrl-C.
        """
        try:
            first = self._lines.get(timeout=timeout)

        except queue.Empty:
            return None

        parts = [first]
        deadline = time.monotonic() + CONFIG.voice.aggregate_seconds

        while True:
            remaining = deadline - time.monotonic()

            if remaining <= 0:
                break

            try:
                parts.append(self._lines.get(timeout=remaining))

            except queue.Empty:
                break

        sentence = " ".join(part.strip() for part in parts if part.strip())

        if len(parts) > 1:
            log.info("joined %d fragments into %r", len(parts), sentence)

        return sentence or None

    # ==================================================================
    # Clear
    # ==================================================================

    def clear(self):
        """
        Drop anything captured but not yet consumed.

        State gating stops most of it, but audio already in flight when the
        state changed would otherwise surface as the next command.
        """
        try:
            while True:
                self._lines.get_nowait()

        except queue.Empty:
            pass

    # ==================================================================
    # Stop
    # ==================================================================

    def stop(self):
        self._running = False

        if self.mic is None:
            return

        for step in ("stop", "close"):
            try:
                getattr(self.mic, step)()

            except Exception:
                log.debug("microphone %s() failed", step, exc_info=True)

    # ==================================================================
    # Keyterm biasing
    # ==================================================================

    def _apply_keyterms(self):
        """
        Bias the decoder towards names that exist on this machine.

        This is the fix for app names that a bigger model does not give:
        the decoder is told 'Brave' and 'VS Code' are plausible words before
        it has to guess.
        """
        terms = list(COMMAND_KEYTERMS)

        if self.apps is not None:
            try:
                for entry in self.apps.entries():
                    name = (entry.get("name") or "").strip()

                    # Commas are rejected by the API, and one bad term would
                    # lose the whole list.
                    if name and "," not in name and len(name) <= 30:
                        terms.append(name)

            except Exception:
                log.exception("could not read the application catalog")

        try:
            from app.core.resolver.catalog import APP_ALIASES, SITES

            terms.extend(SITES.keys())
            terms.extend(APP_ALIASES.keys())

        except Exception:
            log.exception("could not read the site catalog")

        seen, unique = set(), []

        for term in terms:
            key = term.lower()

            if key not in seen and "," not in term:
                seen.add(key)
                unique.append(term)

        unique = unique[:MAX_KEYTERMS]

        try:
            self.mic.set_keyterms(unique)
            log.info("biased the decoder towards %d terms", len(unique))

        except Exception as error:
            # Non-streaming architectures reject keyterms; not fatal.
            log.warning("keyterm biasing unavailable: %s", error)
