from app.core.assistant import Assistant
from app.llm.llama_cpp import LocalLLM

from app.tools.registry import ToolRegistry
from app.tools.filesystem import ListDirectoryTool
from app.tools.windows_shell import OpenWindowsShellTool

from app.tools.system import (
    SystemInfoTool,
    LaunchApplicationTool,
)


def build_assistant():

    tools = ToolRegistry()

    tools.register(SystemInfoTool())
    tools.register(LaunchApplicationTool())
    tools.register(ListDirectoryTool())
    tools.register(OpenWindowsShellTool())

    # print("\nTOOLS:")
    # for tool in tools.schemas():
    #     print(tool)
    
    
    llm = LocalLLM(
        base_url="http://127.0.0.1:8080/v1",
        model=r".\models\assistant.gguf",
    )

    return Assistant(
        llm=llm,
        tools=tools
    )


def main():

    assistant = build_assistant()

    print("EDITH initialized.")
    print("Type 'exit' to quit.\n")

    while True:

        user_input = input("You: ")

        if user_input.lower() == "exit":
            break

        try:

            response = assistant.process(
                user_input
            )

            print(f"EDITH: {response}")

        except Exception as e:

            print(f"ERROR: {e}")


if __name__ == "__main__":
    main()

