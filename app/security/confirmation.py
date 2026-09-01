import re
import time
from dataclasses import dataclass, field

from app.config import CONFIG

_YES = {
    "yes", "y", "yeah", "yep", "yup", "sure", "ok", "okay", "do it",
    "go ahead", "confirm", "confirmed", "proceed", "continue", "please do",
}

_NO = {
    "no", "n", "nope", "cancel", "stop", "abort", "nevermind",
    "never mind", "dont", "don't", "do not", "forget it",
    "nothing", "none", "none of them", "neither", "no thanks",
    "not that", "skip", "leave it",
}

_STRIP = re.compile(r"[^a-z' ]+")

# Keeps digits, so '1' and '2nd' survive normalization.
_STRIP_CHOICE = re.compile(r"[^a-z0-9' ]+")


def parse_answer(text: str) -> bool | None:
    """
    Interpret a reply to a confirmation question.

    Returns None for anything that is not clearly yes or no -- the caller
    must treat that as 'the user moved on', never as consent.
    """
    normalized = " ".join(_STRIP.sub(" ", (text or "").lower()).split())

    if normalized in _YES:
        return True

    if normalized in _NO:
        return False

    return None


_ORDINALS = {
    "1": 0, "one": 0, "first": 0, "1st": 0, "former": 0,
    "2": 1, "two": 1, "second": 1, "2nd": 1, "latter": 1,
    "3": 2, "three": 2, "third": 2, "3rd": 2,
    "4": 3, "four": 3, "fourth": 3, "4th": 3,
    "5": 4, "five": 4, "fifth": 4, "5th": 4,
}

_ORDINAL_NOISE = re.compile(
    r"\b(?:the|one|option|choice|number|no|open|that|please|its|it's)\b",
    re.IGNORECASE,
)


def parse_choice(text: str, count: int) -> int | None:
    """
    Interpret a reply to a 'did you mean...' question as an index.

    Understands '1', 'first', 'the 2nd one', 'last'. Returns None when the
    reply is not a selection, so the caller can treat it as a new request
    rather than picking something the user did not choose.
    """
    if count <= 0:
        return None

    # Digits survive here, unlike the yes/no normalizer -- '1' is the most
    # common way to answer this question.
    normalized = " ".join(_STRIP_CHOICE.sub(" ", (text or "").lower()).split())

    if not normalized:
        return None

    if normalized in ("last", "the last", "last one", "the last one"):
        return count - 1

    # A bare answer such as 'one' or '1'. Checked before noise stripping,
    # because 'one' is also the filler in 'the first one'.
    if normalized in _ORDINALS:
        index = _ORDINALS[normalized]

        return index if index < count else None

    # 'the first one' -> 'first'
    stripped = " ".join(_ORDINAL_NOISE.sub(" ", normalized).split())

    found = {
        _ORDINALS[token]
        for token in stripped.split()
        if token in _ORDINALS
    }

    # 'maybe the first or second' names two options, so it picks neither.
    if len(found) != 1:
        return None

    index = found.pop()

    return index if index < count else None


@dataclass
class PendingClarification:
    """
    An unanswered 'did you mean...' question.

    Held as explicit state so the answer is resolved deterministically,
    instead of being sent to the model as if it were a fresh command.
    """

    question: str
    options: list = field(default_factory=list)
    created_at: float = field(default_factory=time.monotonic)

    def is_expired(self, ttl: float | None = None) -> bool:
        ttl = CONFIG.confirmation_ttl_seconds if ttl is None else ttl
        return time.monotonic() - self.created_at > ttl

    def resolve(self, text: str):
        """Returns the chosen option, or None."""
        index = parse_choice(text, len(self.options))

        if index is not None:
            return self.options[index]

        # A distinctive fragment of one label or path also selects it.
        needle = " ".join(_STRIP.sub(" ", (text or "").lower()).split())

        if len(needle) < 3:
            return None

        hits = [
            option
            for option in self.options
            if needle in str(option.get("label", "")).lower()
            or needle in str(option.get("path", "")).lower()
        ]

        return hits[0] if len(hits) == 1 else None


@dataclass
class PendingConfirmation:
    """
    A destructive operation that has been described but not performed.

    The tool has already done a real dry run, so `summary` reports observed
    facts rather than a prediction the model invented.
    """

    tool_name: str
    arguments: dict
    summary: str
    preview: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)

    def is_expired(self, ttl: float | None = None) -> bool:
        ttl = CONFIG.confirmation_ttl_seconds if ttl is None else ttl
        return time.monotonic() - self.created_at > ttl

    def question(self) -> str:
        return f"{self.summary} Do you want me to continue?"
