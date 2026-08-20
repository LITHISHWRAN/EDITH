import json

from app.core.state import AssistantState
from app.tools.registry import ToolRegistry


class Assistant:

    def __init__(
        self,
        llm,
        tools: ToolRegistry,
    ):
        self.llm = llm
        self.tools = tools
        self.state = AssistantState()

    def process(self, user_input: str):

        self.state.add_message(
            "user",
            user_input,
        )

        max_iterations = 8

        for _ in range(max_iterations):

            response = self.llm.generate(
                messages=self.state.get_messages(),
                tools=self.tools.schemas(),
            )

            message = response.choices[0].message

            # --------------------------------------------------
            # No tool call -> normal final response
            # --------------------------------------------------

            if not message.tool_calls:

                final_response = message.content or ""

                self.state.add_message(
                    "assistant",
                    final_response,
                )

                return final_response

            # --------------------------------------------------
            # Tool call
            # --------------------------------------------------

            assistant_message = {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [],
            }

            for tool_call in message.tool_calls:

                assistant_message["tool_calls"].append({
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                })

            # Store assistant's tool-call message
            self.state.add_raw_message(
                assistant_message
            )

            # --------------------------------------------------
            # Execute tools
            # --------------------------------------------------

            for tool_call in message.tool_calls:

                tool_name = tool_call.function.name

                # Parse arguments
                try:

                    arguments = json.loads(
                        tool_call.function.arguments
                    )

                except json.JSONDecodeError as e:

                    result = {
                        "success": False,
                        "error": f"Invalid tool arguments: {e}",
                    }

                    self.state.add_raw_message({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result),
                    })

                    continue

                # Find tool
                try:

                    tool = self.tools.get(
                        tool_name
                    )

                except KeyError:

                    result = {
                        "success": False,
                        "error": f"Unknown tool: {tool_name}",
                    }

                else:

                    # Execute tool
                    try:

                        result = tool.execute(
                            **arguments
                        )

                    except Exception as e:

                        result = {
                            "success": False,
                            "error": str(e),
                        }

                # Store tool result
                self.state.add_raw_message({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                })

        return (
            "I could not complete the request within "
            "the allowed number of steps."
        )