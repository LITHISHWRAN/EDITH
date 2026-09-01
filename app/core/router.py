
import re
from dataclasses import dataclass, field
from typing import Any

from app.config import CONFIG
from app.core.resolver.catalog import Candidate, EntityCatalog, normalize

TIER_FAST = "fast"
TIER_CLARIFY = "clarify"
TIER_AGENT = "agent"
TIER_CONTROL = "control"

# Commands aimed at EDITH itself rather than at the computer. Handled before
# anything else so they can never be mistaken for a request to search, or
# for an answer to a pending question.
CONTROL_PHRASES = {
    "clear", "cls", "clear chat", "clear the chat", "clear history",
    "clear conversation", "clear context", "new chat", "start over",
    "start again", "reset", "reset chat", "forget everything",
    "forget this conversation", "wipe history",
}

OPEN_VERBS = {
    "open", "launch", "start", "run", "show", "bring up",
    "fire up", "pull up", "go to", "take me to",
}
PLAY_VERBS = {"play", "put on"}
CLOSE_VERBS = {"close", "quit", "exit", "kill", "shut", "shut down", "stop"}

# Exact normalized phrases that map straight to a tool with no arguments.
# Extending this table is how you add fast-path coverage -- not by adding
# another _is_something() method.
PHRASE_INTENTS: dict[str, tuple[str, dict]] = {
    **{p: ("get_current_time", {}) for p in (
        "time", "the time", "what time is it", "whats the time",
        "what is the time", "tell me the time", "current time",
    )},
    **{p: ("get_system_info", {}) for p in (
        "system info", "system information", "computer info",
        "computer information", "what are my system details",
    )},
    **{p: ("read_clipboard", {}) for p in (
        "read my clipboard", "read the clipboard", "whats on my clipboard",
        "what is on my clipboard", "show my clipboard", "paste",
        "what did i copy",
    )},
    **{p: ("clear_clipboard", {}) for p in (
        "clear my clipboard", "clear the clipboard", "empty my clipboard",
    )},
}

_PREFIX = re.compile(
    r"^(?:hey\s+edith|ok\s+edith|edith)\b[\s,]*"
    r"|^(?:please|can you|could you|would you|i want to|i'd like to|"
    r"i would like to)\s+",
    re.IGNORECASE,
)
_SUFFIX = re.compile(r"\s*(?:please|for me|right now|now)\s*[.!?]*$", re.IGNORECASE)
_ARTICLE = re.compile(r"^(?:my|the|a|an|up)\s+", re.IGNORECASE)

_TYPE_HINTS = [
    (re.compile(r"\s+(?:folder|directory)$", re.IGNORECASE), "folder"),
    (re.compile(r"\s+(?:website|site|page)$", re.IGNORECASE), "site"),
    (re.compile(r"\s+(?:app|application|program)$", re.IGNORECASE), "app"),
]


@dataclass
class RouteResult:
    tier: str
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    response: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    reason: str = ""

    @property
    def handled(self) -> bool:
        """Backwards-compatible with the old Assistant contract."""
        return self.tier in (TIER_FAST, TIER_CLARIFY)


class IntentRouter:
    def __init__(self, catalog: EntityCatalog | None = None):
        self.catalog = catalog or EntityCatalog()
        self.settings = CONFIG.router

    def route(self, user_input: str) -> RouteResult:
        text = self._strip_wrapper(user_input)

        if not text:
            return RouteResult(
                tier=TIER_FAST,
                response="Please tell me what you need.",
            )

        if normalize(text) in CONTROL_PHRASES:
            return RouteResult(
                tier=TIER_CONTROL,
                reason="control-phrase",
            )

        phrase = PHRASE_INTENTS.get(normalize(text))

        if phrase:
            tool, arguments = phrase
            return RouteResult(
                tier=TIER_FAST,
                tool_name=tool,
                arguments=arguments,
                reason="phrase-intent",
            )

        verb, obj = self._split_verb(text)

        if verb is None or not obj:
            return RouteResult(tier=TIER_AGENT, reason="no-verb")

        if verb == "play":
            return RouteResult(
                tier=TIER_FAST,
                tool_name="play_media",
                arguments={"query": obj},
                reason="play-verb",
            )

        if verb == "close":
            # Resolution happens in the tool, against processes that are
            # actually running -- the installed-apps catalog cannot tell.
            return RouteResult(
                tier=TIER_FAST,
                tool_name="close_application",
                arguments={"application": obj},
                reason="close-verb",
            )

        return self._route_open(obj)

    # ------------------------------------------------------------------

    def _route_open(self, obj: str) -> RouteResult:
        obj, hint = self._extract_hint(obj)

        candidates = self.catalog.resolve(obj, hint=hint)

        if not candidates:
            return RouteResult(
                tier=TIER_AGENT,
                reason="no-candidates",
            )

        best = candidates[0]
        runner_up = candidates[1] if len(candidates) > 1 else None

        if best.score < self.settings.floor_threshold:
            return RouteResult(tier=TIER_AGENT, reason="low-confidence")

        contested = (
            runner_up is not None
            and best.score - runner_up.score < self.settings.ambiguity_margin
        )

        if best.score >= self.settings.confident_threshold and not contested:
            return RouteResult(
                tier=TIER_FAST,
                tool_name=best.tool,
                arguments=best.arguments,
                candidates=candidates,
                reason=f"resolved:{best.kind}",
            )

        if contested:
            return RouteResult(
                tier=TIER_CLARIFY,
                response=self._clarification(candidates[:3]),
                candidates=candidates[:3],
                reason="ambiguous",
            )

        # Single plausible-but-not-certain match. Low-risk action, so take it
        # rather than interrogating the user (requirement 18).
        return RouteResult(
            tier=TIER_FAST,
            tool_name=best.tool,
            arguments=best.arguments,
            candidates=candidates,
            reason=f"best-effort:{best.kind}",
        )

    @staticmethod
    def _clarification(candidates: list[Candidate]) -> str:
        labels = [candidate.label for candidate in candidates]

        # Same-named folders in different places: the name cannot tell them
        # apart, so show the paths instead.
        if len(set(labels)) < len(labels):
            options = " or ".join(
                candidate.arguments.get("path", candidate.label)
                for candidate in candidates
            )

            return f"I found several. Did you mean {options}?"

        options = " or ".join(
            f"the {candidate.label} {candidate.kind}"
            for candidate in candidates
        )

        return f"Did you mean {options}?"

    @staticmethod
    def _strip_wrapper(text: str) -> str:
        text = text.strip()

        while True:
            stripped = _PREFIX.sub("", text, count=1).strip()

            if stripped == text:
                break

            text = stripped

        text = _SUFFIX.sub("", text).strip()

        return text.rstrip("?.!").strip()

    @staticmethod
    def _split_verb(text: str) -> tuple[str | None, str]:
        lowered = text.lower()

        for phrase in sorted(
            OPEN_VERBS | PLAY_VERBS | CLOSE_VERBS, key=len, reverse=True
        ):
            if lowered.startswith(phrase + " "):
                obj = _ARTICLE.sub("", text[len(phrase):].strip()).strip()

                if phrase in PLAY_VERBS:
                    return "play", obj

                if phrase in CLOSE_VERBS:
                    return "close", obj

                return "open", obj

        return None, ""

    @staticmethod
    def _extract_hint(obj: str) -> tuple[str, str | None]:
        for pattern, kind in _TYPE_HINTS:
            if pattern.search(obj):
                return pattern.sub("", obj).strip(), kind

        return obj, None
