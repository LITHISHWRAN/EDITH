from dataclasses import dataclass, field


@dataclass
class AssistantState:

    conversation: list = field(
        default_factory=list
    )

    def __post_init__(self):

        from app.core.prompts import SYSTEM_PROMPT

        self.conversation.append({
            "role": "system",
            "content": SYSTEM_PROMPT,
        })

    def add_message(
        self,
        role: str,
        content: str,
    ):

        self.conversation.append({
            "role": role,
            "content": content,
        })

    def add_raw_message(
        self,
        message: dict,
    ):

        self.conversation.append(message)

    def get_messages(self):

        return self.conversation