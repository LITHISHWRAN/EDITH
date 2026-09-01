from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):

    name: str
    description: str
    parameters: dict

    # Consumed by SecurityPolicy, never sent to the model. Keeping it out of
    # schema() matters: the OpenAI function schema is a strict contract, and
    # extra keys either get rejected or waste tokens on every single turn.
    risk: str = "safe"  # safe | sensitive | destructive

    @abstractmethod
    def execute(self, **kwargs) -> Any:
        pass

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }