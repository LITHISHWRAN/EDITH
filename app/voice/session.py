"""
The voice loop: listen, act, speak.

Listening is continuous, so there is no window to speak into and nothing to
miss. What decides whether audio counts is the state, not the microphone --
see voice/state.py. That covers the thinking phase as well as the speaking
one, which muting alone would leave open.

Everything downstream of the transcript is the code the typed interface
uses, so a spoken 'open chrome' takes the same fast path and gets the same
confirmation before anything destructive.
"""

import time

from app.config import CONFIG
from app.observability.logging_setup import get_logger
from app.voice.state import VoiceState, VoiceStateManager
from app.voice.stt import MoonshineSTT
from app.voice.tts import Speaker

log = get_logger("voice.session")

STOP_WORDS = {
    "exit", "quit", "stop listening", "goodbye", "good bye",
    "that's all", "thats all",
}


class VoiceSession:

    def __init__(self, assistant, speak_replies: bool = True):
        self.assistant = assistant
        self.state = VoiceStateManager()

        catalog = getattr(assistant.router, "catalog", None)

        self.stt = MoonshineSTT(
            state=self.state,
            apps_index=getattr(catalog, "apps", None),
        )

        self.speaker = Speaker() if speak_replies else None

    # ------------------------------------------------------------------

    def run(self):
        print("Loading speech models...")

        if self.speaker:
            self.speaker.warm()

        # Listening before the microphone opens, not after: anything said in
        # between would otherwise be transcribed and then discarded for
        # arriving in the wrong state.
        self.state.listening()
        self.stt.start(on_partial=self._show_partial)

        print("\nListening. Speak whenever you like; say 'exit' to stop.\n")

        try:
            self._loop()

        except KeyboardInterrupt:
            print()

        finally:
            self.state.idle()
            self.stt.stop()

    def _loop(self):
        while True:
            # A timeout rather than an unbounded wait, so Ctrl-C is felt.
            text = self.stt.wait_for_sentence(timeout=0.5)

            if not text:
                continue

            text = text.strip()

            if not text:
                continue

            print(f"\rYou: {text}{' ' * 30}")

            if self._is_stop(text):
                with self.state.busy(VoiceState.SPEAKING):
                    self._reply("Goodbye.")

                break

            reply = self._turn(text)

            print(f"EDITH: {reply}")

    def _turn(self, text: str) -> str:
        """
        One exchange. Deaf from the moment the command is accepted until
        well after the reply has finished playing.

        The state is held across both phases rather than returning to
        LISTENING in between: busy() restores on exit, and that gap was long
        enough for the microphone to unmute and catch the start of EDITH's
        own answer.
        """
        try:
            self.state.set(VoiceState.THINKING)
            reply = self._respond(text)

            self.state.set(VoiceState.SPEAKING)
            self._reply(reply)

            return reply

        finally:
            # Order matters. The microphone stays muted while the tail of
            # EDITH's voice drains from the audio pipeline; only then is
            # the queue emptied and listening resumed. Unmuting first would
            # let the last syllable back in.
            time.sleep(CONFIG.voice.settle_seconds)
            self.stt.clear()
            self.state.listening()
            self.stt.clear()

    # ------------------------------------------------------------------

    def _respond(self, text: str) -> str:
        try:
            return self.assistant.process(text)

        except Exception:
            log.exception("voice turn failed: %r", text)

            return "Something went wrong on my side."

    def _reply(self, text: str):
        if not self.speaker:
            return

        try:
            self.speaker.say(text)

        except Exception:
            log.exception("speech output failed")

    @staticmethod
    def _show_partial(text: str):
        if text:
            print(f"\r  ...{text[-70:]}", end="", flush=True)

    @staticmethod
    def _is_stop(text: str) -> bool:
        return text.lower().strip(" .,!?") in STOP_WORDS
