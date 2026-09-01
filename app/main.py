import sys

from app.config import CONFIG
from app.core.assistant import Assistant
from app.core.resolver.apps_index import AppsIndex
from app.core.resolver.catalog import EntityCatalog
from app.core.resolver.folder_index import FolderIndex
from app.observability.logging_setup import get_logger, setup as _setup_logging


def setup_logging():
    _setup_logging()
    return get_logger("main")
from app.core.router import IntentRouter
from app.llm.llama_cpp import LocalLLM

from app.tools.registry import ToolRegistry
from app.tools.clipboard import (
    ClearClipboardTool,
    ReadClipboardTool,
    WriteClipboardTool,
)
from app.tools.file_content import ReadFileTool, WriteFileTool
from app.tools.filesystem import (
    CopyFilesTool,
    CreateFolderTool,
    DeleteFilesTool,
    DuplicateItemTool,
    FindFilesTool,
    ListDirectoryTool,
    MoveFilesTool,
    OpenFileTool,
    OpenFolderTool,
    RenameItemTool,
)
from app.tools.windows_shell import OpenWindowsShellTool
from app.tools.browser import (
    OpenWebsiteTool,
    PlayMediaTool,
    WebSearchTool,
)

from app.tools.process import CloseApplicationTool
from app.tools.system import (
    SystemInfoTool,
    GetCurrentTimeTool,
    LaunchApplicationTool,
)


def build_assistant():

    # One index, shared by the router and the launcher, so the PowerShell
    # scan happens once per session instead of once per command.
    apps_index = AppsIndex()
    apps_index.warm_async()

    # ~1.4s to walk D: cold, so build it off the critical path too.
    folder_index = FolderIndex()
    folder_index.warm_async()

    tools = ToolRegistry()

    tools.register(SystemInfoTool())
    tools.register(GetCurrentTimeTool())
    tools.register(LaunchApplicationTool(apps_index=apps_index))
    tools.register(CloseApplicationTool(apps_index=apps_index))
    tools.register(ListDirectoryTool())
    tools.register(OpenFolderTool(folder_index=folder_index))
    tools.register(OpenFileTool())
    tools.register(CreateFolderTool())
    tools.register(CopyFilesTool())
    tools.register(MoveFilesTool())
    tools.register(DuplicateItemTool())
    tools.register(DeleteFilesTool())
    tools.register(RenameItemTool())
    tools.register(FindFilesTool())
    tools.register(ReadFileTool())
    tools.register(WriteFileTool())
    tools.register(OpenWindowsShellTool())
    tools.register(OpenWebsiteTool())
    tools.register(PlayMediaTool())
    tools.register(WebSearchTool())
    tools.register(ReadClipboardTool())
    tools.register(WriteClipboardTool())
    tools.register(ClearClipboardTool())

    llm = LocalLLM(
        base_url=CONFIG.llm.base_url,
        model=CONFIG.llm.model,
    )

    router = IntentRouter(
        catalog=EntityCatalog(
            apps_index=apps_index,
            folder_index=folder_index,
        )
    )

    return Assistant(
        llm=llm,
        tools=tools,
        router=router,
        folder_index=folder_index,
    )


def main():

    # The Windows console defaults to cp1252, which turns ordinary
    # punctuation from web pages and documents into replacement characters.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    log = setup_logging()

    voice_mode = "--voice" in sys.argv or "-v" in sys.argv

    log.info(
        "EDITH starting (context=%s, voice=%s)",
        CONFIG.llm.context_tokens, voice_mode,
    )

    try:
        assistant = build_assistant()

    except Exception:
        log.exception("Startup failed")
        print("EDITH could not start. See logs/edith.log for details.")
        return

    print("EDITH initialized.")

    if voice_mode:
        from app.voice.session import VoiceSession

        try:
            VoiceSession(assistant).run()

        except KeyboardInterrupt:
            print()

        except Exception:
            log.exception("Voice session failed")
            print("Voice mode failed. See logs/edith.log for details.")

        log.info("EDITH stopped")
        return

    print("Type 'exit' to quit.\n")

    while True:

        try:
            user_input = input("You: ")

        except (EOFError, KeyboardInterrupt):
            print()
            break

        if user_input.strip().lower() in {"exit", "quit"}:
            break

        try:

            response = assistant.process(
                user_input
            )

            print(f"EDITH: {response}")

        except Exception:

            # exception() keeps the traceback. Printing str(e) discarded the
            # one thing that makes a failure diagnosable after the fact.
            log.exception("Turn failed: %r", user_input)

            print(
                "EDITH: Something went wrong on my side. "
                "The details are in logs/edith.log."
            )

    log.info("EDITH stopped")


if __name__ == "__main__":
    main()
