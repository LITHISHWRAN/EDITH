import json

from app.config import CONFIG

# Measured against the server's tokenizer: the densest real content came in
# at 3.19 characters per token, so 3.0 over-estimates slightly and never
# under-estimates. Cheaper than a /tokenize round-trip on every turn.
CHARS_PER_TOKEN = 3.0

# What a stale tool result is reduced to. The model has already used it to
# write its answer; keeping the full page text or file contents around costs
# ~1500 tokens each and buys nothing.
STALE_TOOL_CHARS = 180


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def message_tokens(message: dict) -> int:
    content = message.get("content") or ""
    total = estimate_tokens(content) + 4  # role and framing overhead

    for call in message.get("tool_calls") or []:
        function = call.get("function", {})
        total += estimate_tokens(
            f"{function.get('name', '')}{function.get('arguments', '')}"
        )

    return total


class Conversation:
    """
    Bounded history.

    Bounded by *tokens*, not turns: a single web search carries thousands of
    tokens of page text, so a turn count says nothing about whether the next
    request will fit. The system prompt is pinned so llama.cpp's prompt cache
    keeps hitting; only the tail rotates.
    """

    def __init__(self, system_prompt: str, budget_tokens: int | None = None):
        self._system = {"role": "system", "content": system_prompt}
        self._turns: list[dict] = []
        self._budget = budget_tokens or CONFIG.llm.history_token_budget

    # ------------------------------------------------------------------

    def add(self, role: str, content: str):
        if role == "user":
            # The previous turn is finished, so its bulky tool payloads are
            # spent. Shrinking them here is what stops two web searches from
            # filling the whole context window.
            self._compact_tool_results()

        self._turns.append({"role": role, "content": content})
        self._trim()

    def add_raw(self, message: dict):
        self._turns.append(message)
        self._trim()

    def messages(self) -> list[dict]:
        return [self._system, *self._turns]

    def tokens(self) -> int:
        return sum(message_tokens(m) for m in self.messages())

    # ------------------------------------------------------------------

    def _compact_tool_results(self):
        for message in self._turns:
            if message.get("role") != "tool":
                continue

            content = message.get("content") or ""

            if len(content) <= STALE_TOOL_CHARS:
                continue

            message["content"] = (
                content[:STALE_TOOL_CHARS] + " ...[earlier result trimmed]"
            )

    def _trim(self, budget: int | None = None):
        budget = budget or self._budget

        while self._turns and self._used() > budget:
            # Drop whole exchanges from the front. A tool result whose call
            # has been dropped would leave the model reading an answer to a
            # question it cannot see.
            self._turns.pop(0)

            while self._turns and self._turns[0]["role"] == "tool":
                self._turns.pop(0)

    def _used(self) -> int:
        return sum(message_tokens(m) for m in self._turns)

    # ------------------------------------------------------------------

    def shrink_for_retry(self) -> bool:
        """
        Emergency trim after the server rejected a request as too long.

        Returns False when there is nothing left to give up, so the caller
        stops retrying instead of looping.
        """
        self._compact_tool_results()

        if len(self._turns) <= 1:
            return False

        # Halve the history, keeping the most recent exchange.
        keep = max(1, len(self._turns) // 2)
        self._turns = self._turns[-keep:]

        while self._turns and self._turns[0]["role"] == "tool":
            self._turns.pop(0)

        return True

    def clear(self):
        """Start over. The system prompt stays; everything else goes."""
        self._turns = []

    def reset_to_last_user_message(self):
        """Last resort: keep only what the user just asked."""
        for message in reversed(self._turns):
            if message.get("role") == "user":
                self._turns = [message]
                return

        self._turns = []
