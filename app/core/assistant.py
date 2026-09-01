import json
from pathlib import Path

from app.config import CONFIG
from app.core.prompts import build_system_prompt
from app.core.memory.conversation import Conversation
from app.core.memory.task_state import TaskState
from app.core.resolver.catalog import normalize
from app.core.router import (
    CONTROL_PHRASES,
    TIER_CLARIFY,
    TIER_CONTROL,
    IntentRouter,
)
from app.core.summarize import for_model
from app.observability.logging_setup import get_logger
from app.observability.trace import Trace

log = get_logger("assistant")
from app.security.confirmation import (
    PendingClarification,
    PendingConfirmation,
    parse_answer,
)
from app.tools.registry import ToolRegistry


class Assistant:

    def __init__(
        self,
        llm,
        tools: ToolRegistry,
        router: IntentRouter | None = None,
        folder_index=None,
    ):
        self.llm = llm
        self.tools = tools
        self.state = Conversation(build_system_prompt())
        self.task_state = TaskState()
        self.folder_index = folder_index
        self.router = IntentRouter() if router is None else router
        self.pending: PendingConfirmation | None = None
        self.pending_choice: PendingClarification | None = None

    def process(self, user_input: str) -> str:
        trace = Trace(utterance=user_input)

        try:
            return self._process(user_input, trace)

        finally:
            trace.flush()

    def _process(self, user_input: str, trace: Trace) -> str:
        # 'clear' is aimed at EDITH, not at the computer, and it has to work
        # even while a confirmation is outstanding -- otherwise the way out
        # of a stuck conversation would itself be blocked.
        if normalize(user_input) in CONTROL_PHRASES:
            trace.note(tier=TIER_CONTROL, reason="control-phrase")

            return self._clear_conversation()

        # Answering an outstanding question comes before routing: 'yes' is
        # not a command and must never be resolved as one.
        if self.pending is not None:
            with trace.span("confirmation"):
                answered = self._resolve_pending(user_input)

            if answered is not None:
                trace.note(tier="confirmation")
                self.state.add("user", user_input)
                self.state.add("assistant", answered)

                return answered

        # Same rule as confirmations: an answer to an outstanding question is
        # not a new command and must not be routed as one.
        if self.pending_choice is not None:
            with trace.span("clarification"):
                answered = self._resolve_choice(user_input)

            if answered is not None:
                trace.note(tier="clarification")
                self.state.add("user", user_input)
                self.state.add("assistant", answered)

                return answered

        self.state.add("user", user_input)

        with trace.span("route"):
            route = self.router.route(user_input)

        trace.note(tier=route.tier, reason=route.reason, tool=route.tool_name)

        log.info(
            "route %r -> tier=%s tool=%s (%s)",
            user_input, route.tier, route.tool_name, route.reason,
        )

        if route.tier == TIER_CLARIFY and route.candidates:
            self.pending_choice = PendingClarification(
                question=route.response,
                options=[
                    {
                        "label": candidate.label,
                        "tool": candidate.tool,
                        "arguments": candidate.arguments,
                        "path": candidate.arguments.get("path", ""),
                    }
                    for candidate in route.candidates
                ],
            )

            self.state.add("assistant", route.response)

            return route.response

        if route.handled:
            with trace.span("fast_path", tool=route.tool_name):
                response = self._handle_fast_result(route)

            self.state.add("assistant", response)

            return response

        with trace.span("agent_path"):
            return self._handle_agent_request(user_input, trace)

    # ------------------------------------------------------------------
    # Confirmation
    # ------------------------------------------------------------------

    def _resolve_choice(self, user_input: str) -> str | None:
        """
        Returns a reply if this turn answered the 'did you mean' question,
        else None (the user moved on, so route normally).
        """
        pending = self.pending_choice

        if pending.is_expired():
            self.pending_choice = None
            return None

        if parse_answer(user_input) is False:
            self.pending_choice = None
            return "Alright, never mind."

        chosen = pending.resolve(user_input)

        if chosen is None:
            # Not a selection. Drop the question rather than guessing.
            self.pending_choice = None
            return None

        self.pending_choice = None

        try:
            result = self.tools.get(chosen["tool"]).execute(**chosen["arguments"])

        except Exception as error:
            return f"The operation failed: {error}"

        if not isinstance(result, dict):
            return str(result)

        self._record(chosen["tool"], result)

        if result.get("success") is False:
            return result.get("error", "The operation failed.")

        return self._format_tool_success(chosen["tool"], result)

    def _resolve_pending(self, user_input: str) -> str | None:
        """
        Returns a reply if this turn settled the pending operation, else None
        (meaning: the user moved on, so route normally).
        """
        pending = self.pending

        if pending.is_expired():
            self.pending = None
            return None

        answer = parse_answer(user_input)

        if answer is None:
            # Anything that is not a clear yes or no is not consent.
            self.pending = None
            return None

        self.pending = None

        if answer is False:
            return "Cancelled. Nothing was changed."

        arguments = {**pending.arguments, "_confirmed": True}

        try:
            result = self.tools.get(pending.tool_name).execute(**arguments)

        except Exception as error:
            return f"The operation failed: {error}"

        self._record(pending.tool_name, result)

        if isinstance(result, dict) and result.get("success") is False:
            return result.get("error", "The operation failed.")

        return self._format_tool_success(pending.tool_name, result)

    def _clear_conversation(self) -> str:
        """Wipe everything the conversation is carrying, questions included."""
        self.state.clear()
        self.task_state = TaskState()
        self.pending = None
        self.pending_choice = None

        log.info("conversation cleared")

        return "Conversation cleared."

    def _record(self, tool_name: str, result) -> None:
        """Single place where a successful tool updates EDITH's own state."""
        self.task_state.record(tool_name, result)

        if self.folder_index is not None:
            self.folder_index.sync(tool_name, result)

    def _store_choice(self, tool_name: str, result: dict) -> str:
        """
        A tool found several equally good targets. Turn them into the same
        answerable question the router uses, so 'the first one' works here
        too rather than being routed as a new command.
        """
        matches = result["matches"]

        options = [
            {
                "label": match.get("name", ""),
                "tool": tool_name,
                "arguments": {"path": match["path"]},
                "path": match["path"],
            }
            for match in matches
        ]

        listed = " or ".join(match["path"] for match in matches)
        question = f"I found several. Did you mean {listed}?"

        self.pending_choice = PendingClarification(
            question=question,
            options=options,
        )

        return question

    def _store_confirmation(self, tool_name: str, arguments: dict, result: dict) -> str:
        self.pending = PendingConfirmation(
            tool_name=tool_name,
            arguments=arguments,
            summary=result.get("summary", "This will change files."),
            preview=result.get("preview", {}),
        )

        return self.pending.question()

    def _handle_fast_result(self, route_result) -> str:
        if not route_result.tool_name:
            return route_result.response

        try:
            tool = self.tools.get(
                route_result.tool_name
            )

        except KeyError:
            return (
                f"I do not have a tool named "
                f"'{route_result.tool_name}'."
            )

        try:
            result = tool.execute(
                **(route_result.arguments or {})
            )

        except Exception as error:
            return f"The operation failed: {error}"

        if not isinstance(result, dict):
            return str(result)

        if result.get("requires_confirmation"):
            return self._store_confirmation(
                route_result.tool_name,
                route_result.arguments or {},
                result,
            )

        if result.get("ambiguous") and result.get("matches"):
            return self._store_choice(route_result.tool_name, result)

        self._record(route_result.tool_name, result)

        if result.get("success") is False:
            return result.get(
                "error",
                "The operation failed.",
            )

        return self._format_tool_success(
            route_result.tool_name,
            result,
        )


    def _format_tool_success(
        self,
        tool_name: str,
        result: dict,
    ) -> str:
        if tool_name == "launch_application":
            application = result.get(
                "application",
                "the application",
            )

            # Three different claims for three different states of evidence.
            # Only the first one asserts that the app is actually running.
            verified = result.get("verified")

            if verified is True:
                if result.get("reason") == "already-running":
                    return f"{application} is already open."

                return f"{application} is open."

            if verified is False:
                return (
                    f"I started {application}, but I couldn't confirm "
                    f"it came up."
                )

            return f"I've launched {application}."

        if tool_name == "close_application":
            application = result.get("application", "the application")
            closed = result.get("closed", 0)

            if result.get("forced"):
                return f"I had to force {application} to quit."

            if result.get("to_tray"):
                # Covers both tray apps (Slack, Discord) and packaged apps
                # that suspend rather than exit. In both cases the window is
                # genuinely gone, which is what the user asked for.
                return (
                    f"Closed the {application} window. It's still running "
                    f"in the background."
                )

            if closed > 1:
                return f"Closed {closed} {application} windows."

            return f"{application} is closed."

        if tool_name == "open_windows_shell":
            label = result.get("location", "").replace("_", " ").title()

            return f"Opened the {label or 'location'}."

        if tool_name == "open_file":
            return f"Opened {result['name']}."

        if tool_name == "open_folder":
            if result.get("matched_by_name"):
                return f"Opened {result['name']} at {result['path']}."

            return f"Opened {result.get('name', 'the folder')}."

        if tool_name == "copy_files":
            copied = result.get("copied", 0)

            if copied == 0 and "message" in result:
                return result["message"]

            if "name" in result:
                return f"Copied {result['name']} into {Path(result['destination']).name}."

            sentence = f"Copied {copied} file{'' if copied == 1 else 's'}."

            if result.get("failed"):
                sentence += f" {result['failed']} could not be copied."

            return sentence

        if tool_name == "duplicate_item":
            return f"Duplicated {result['original']} as {result['name']}."

        if tool_name == "read_file":
            if result.get("pages"):
                sentence = f"{result['name']} has {result['pages']} pages."
            else:
                sentence = f"{result['name']} has {result['lines']} lines."

            if result.get("truncated"):
                sentence += " I only read the beginning of it."

            return sentence

        if tool_name == "write_file":
            if result.get("appended"):
                return f"Added {result['characters']} characters to {result['name']}."

            verb = "Replaced" if result.get("replaced") else "Saved"

            return f"{verb} {result['name']} with {result['lines']} lines."

        if tool_name == "find_files":
            count = result.get("count", 0)

            if count == 0:
                return f"I found no files matching '{result['query']}'."

            sentence = (
                f"I found {count} file{'' if count == 1 else 's'} "
                f"matching '{result['query']}'."
            )

            # Never let a capped list read as a complete one.
            if result.get("truncated"):
                sentence += " That is the cap, so there may be more."

            return sentence

        if tool_name == "create_folder":
            if result.get("already_existed"):
                return f"{result['name']} already exists."

            return f"Created {result['name']}."

        if tool_name == "move_files":
            moved = result.get("moved", 0)

            if "name" in result:
                kind = "folder" if result.get("is_folder") else "file"

                return (
                    f"Moved the {result['name']} {kind} into "
                    f"{Path(result['destination']).name}."
                )

            sentence = f"Moved {moved} file{'' if moved == 1 else 's'}."

            if result.get("failed"):
                sentence += f" {result['failed']} could not be moved."

            return sentence

        if tool_name == "delete_files":
            deleted = result.get("deleted", 0)

            if "name" in result:
                kind = "folder" if result.get("is_folder") else "file"

                return (
                    f"Moved the {result['name']} {kind} to the Recycle Bin. "
                    f"You can restore it from there."
                )

            sentence = (
                f"Moved {deleted} file{'' if deleted == 1 else 's'} "
                f"to the Recycle Bin."
            )

            if result.get("failed"):
                sentence += f" {result['failed']} could not be deleted."

            return sentence

        if tool_name == "rename_item":
            return f"Renamed {result['old_name']} to {result['new_name']}."

        if tool_name == "write_clipboard":
            return f"Copied {result['length']} characters to your clipboard."

        if tool_name == "read_clipboard":
            if result.get("empty"):
                return "Your clipboard is empty."

            return result["content"]

        if tool_name == "clear_clipboard":
            return "Clipboard cleared."

        if tool_name == "play_media":
            if result.get("playing"):
                return f"Playing {result.get('title') or result['query']}."

            return (
                f"I couldn't resolve a video, so I opened the YouTube "
                f"search for '{result['query']}'."
            )

        if tool_name == "list_directory":
            path = result.get("path", "the directory")
            count = result.get("count", 0)

            return (
                f"I found {count} item(s) in {path}."
            )

        if tool_name == "get_system_info":
            operating_system = result.get(
                "operating_system",
                "the operating system",
            )
            python_version = result.get(
                "python_version",
                "an unknown version",
            )

            return (
                f"You are running {operating_system}. "
                f"Python version: {python_version}."
            )
        
        if tool_name == "open_website":
            browser = result.get(
                "browser",
                "the browser",
            )

            return (
                f"Opened {result['url']} "
                f"using {browser}."
            )

        if tool_name == "search_web":
            # Reached only if something fast-paths a search; the agent path
            # answers from the returned text instead.
            count = len(result.get("results", []))

            return (
                f"I found {count} source{'' if count == 1 else 's'} "
                f"for '{result['query']}'."
            )

        if tool_name == "get_current_time":
            return f"It is {result['time']}."

        return "The operation completed successfully."

    @staticmethod
    def _is_context_overflow(error: Exception) -> bool:
        text = str(error).lower()

        return (
            "exceed_context_size" in text
            or "exceeds the available context" in text
            or "context size" in text and "exceed" in text
        )

    def _generate(self, messages: list, trace: Trace):
        """
        Call the model, giving up history rather than the whole turn if the
        request will not fit.

        Without this a single oversized turn poisons the session: the
        offending messages stay in history, so every later request -- even
        'hi' -- fails with the same error.
        """
        attempt = 0

        while True:
            try:
                return self.llm.generate(
                    messages=messages,
                    tools=self.tools.schemas(),
                )

            except Exception as error:
                if not self._is_context_overflow(error) or attempt >= 2:
                    if self._is_context_overflow(error):
                        # Out of things to drop. Clear rather than leave the
                        # session permanently broken.
                        self.state.reset_to_last_user_message()
                        trace.note(context_recovery="cleared")

                        log.error(
                            "context overflow could not be recovered; "
                            "history cleared"
                        )

                        return None

                    raise

                attempt += 1
                trace.note(context_recovery=f"shrink-{attempt}")

                log.warning(
                    "context overflow (attempt %s), shrinking history from "
                    "%s tokens", attempt, self.state.tokens(),
                )

                if not self.state.shrink_for_retry():
                    self.state.reset_to_last_user_message()

                messages = self.state.messages()

    def _handle_agent_request(self, user_input: str, trace: Trace) -> str:
        # Task context costs tokens on every turn, so only pay for it when
        # the utterance actually contains something to resolve.
        context = ""

        if TaskState.mentions_referent(user_input):
            context = self.task_state.as_context()

            if context:
                trace.note(context_injected=True)

        for step in range(CONFIG.llm.max_agent_steps):
            messages = self.state.messages()

            if context:
                messages = [*messages, {"role": "system", "content": context}]

            with trace.span(f"llm_step_{step}"):
                response = self._generate(messages, trace)

            if response is None:
                return (
                    "That was too much for me to hold in context, and I "
                    "couldn't shrink it enough. I've cleared the history -- "
                    "please ask again."
                )

            message = response.choices[0].message

            if not message.tool_calls:
                final_response = message.content or ""

                self.state.add(
                    "assistant",
                    final_response,
                )

                return final_response

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

            self.state.add_raw(
                assistant_message
            )

            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name

                with trace.span("tool", tool=tool_name):
                    result = self._execute_agent_tool(
                        tool_call
                    )

                self.state.add_raw({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": for_model(tool_name, result),
                })

                if isinstance(result, dict) and result.get("requires_confirmation"):
                    # Leave the loop and put the question to the user
                    # directly. Letting the model keep going here would let
                    # it narrate its way past an unanswered confirmation.
                    trace.note(awaiting_confirmation=tool_name)

                    question = self._store_confirmation(
                        tool_name,
                        self._clean_arguments(tool_call),
                        result,
                    )

                    self.state.add("assistant", question)

                    return question

                self._record(tool_name, result)

        return (
            "I could not complete the request within "
            "the allowed number of steps."
        )

    @staticmethod
    def _clean_arguments(tool_call) -> dict:
        try:
            arguments = json.loads(tool_call.function.arguments)

        except json.JSONDecodeError:
            return {}

        if not isinstance(arguments, dict):
            return {}

        # Private parameters such as _confirmed are not part of any tool
        # schema. Stripping them is what stops the model from approving its
        # own destructive operation.
        return {
            key: value
            for key, value in arguments.items()
            if not key.startswith("_")
        }

    def _execute_agent_tool(self, tool_call) -> dict:
        tool_name = tool_call.function.name

        try:
            json.loads(tool_call.function.arguments)

        except json.JSONDecodeError as error:
            return {
                "success": False,
                "error": f"Invalid tool arguments: {error}",
            }

        arguments = self._clean_arguments(tool_call)

        try:
            tool = self.tools.get(tool_name)

        except KeyError:
            return {
                "success": False,
                "error": f"Unknown tool: {tool_name}",
            }

        log.info("tool %s args=%s", tool_name, arguments)

        try:
            result = tool.execute(
                **arguments
            )

        except Exception as error:
            # The model only ever saw str(error); the traceback was gone.
            log.exception("tool %s raised (args=%s)", tool_name, arguments)

            return {
                "success": False,
                "error": str(error),
            }

        if isinstance(result, dict) and result.get("success") is False:
            log.warning(
                "tool %s failed: %s", tool_name, result.get("error", "")
            )

        return result