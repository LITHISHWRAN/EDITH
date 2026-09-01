from openai import OpenAI

from app.config import CONFIG
from app.llm.base import LLM


class LocalLLM(LLM):

    def __init__(
        self,
        base_url: str,
        model: str,
        enable_thinking: bool | None = None,
    ):

        self.client = OpenAI(
            base_url=base_url,
            api_key="local",
            timeout=CONFIG.llm.request_timeout,
            max_retries=1,
        )

        self.model = model

        # Qwen3 reasons before answering by default. Measured on this
        # machine, that cost 4.33s / 165 reasoning tokens to answer "name
        # three colours" versus 0.22s / 7 tokens with it off -- and an
        # assistant that dispatches tools does not need chain-of-thought
        # for the overwhelming majority of turns. Turn it on deliberately
        # for genuinely hard planning, not globally.
        self.enable_thinking = (
            CONFIG.llm.enable_thinking
            if enable_thinking is None
            else enable_thinking
        )

    def generate(self, messages, tools=None, enable_thinking=None):

        thinking = (
            self.enable_thinking
            if enable_thinking is None
            else enable_thinking
        )

        return self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools or None,
            extra_body={
                "chat_template_kwargs": {
                    "enable_thinking": thinking,
                },
            },
        )
