from openai import OpenAI


class LocalLLM:

    def __init__(
        self,
        base_url: str,
        model: str,
    ):

        self.client = OpenAI(
            base_url=base_url,
            api_key="local",
        )

        self.model = model

    def generate(self, messages, tools=None):

        return self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools or None,
        )