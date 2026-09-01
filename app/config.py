
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
CACHE_DIR = Path.home() / ".edith"


def _env(key: str, default: str) -> str:
    value = os.environ.get(key, "").strip()
    return value or default


@dataclass(frozen=True)
class LLMConfig:
    base_url: str = _env("LLAMA_SERVER_URL", "http://127.0.0.1:8080/v1")
    model: str = _env("LLAMA_MODEL", "assistant")
    request_timeout: float = float(_env("LLAMA_TIMEOUT", "90"))
    max_agent_steps: int = int(_env("MAX_AGENT_STEPS", "8"))
    max_history_turns: int = int(_env("MAX_HISTORY_TURNS", "12"))
    # The server's -c value. Everything below is derived from it.
    context_tokens: int = int(_env("LLAMA_CONTEXT", "8192"))
    # Measured on this build: 23 tool schemas cost ~2900 tokens and the
    # system prompt ~430, on every single turn. Reserve for that plus room
    # for the reply, and what remains is what history may use.
    fixed_overhead_tokens: int = int(_env("LLAMA_FIXED_OVERHEAD", "3400"))
    response_reserve_tokens: int = int(_env("LLAMA_RESPONSE_RESERVE", "1200"))
    # Qwen3 chain-of-thought. Off by default: measured 20x slower on this
    # machine for no gain on tool dispatch.
    enable_thinking: bool = _env("LLAMA_THINKING", "0") == "1"

    @property
    def history_token_budget(self) -> int:
        return max(
            512,
            self.context_tokens
            - self.fixed_overhead_tokens
            - self.response_reserve_tokens,
        )


@dataclass(frozen=True)
class VoiceConfig:
    # --- speech out (Kokoro) ---------------------------------------
    model_path: Path = ROOT / "models" / "voice" / "kokoro-v1.0.onnx"
    voices_path: Path = ROOT / "models" / "voice" / "voices-v1.0.bin"
    voice: str = _env("EDITH_VOICE", "af_heart")
    speed: float = float(_env("EDITH_VOICE_SPEED", "1.0"))
    language: str = _env("EDITH_VOICE_LANG", "en-us")

    # --- speech in (Moonshine streaming) ---------------------------
    # A streaming architecture: it listens continuously and endpoints on
    # its own, which removed the hand-tuned noise threshold that kept
    # clipping the start of commands. Streaming models are also the only
    # ones that accept keyterm biasing.
    stt_model: str = _env("EDITH_STT_MODEL", "SMALL_STREAMING")
    stt_language: str = _env("EDITH_STT_LANG", "en")
    # How often the streaming decoder emits an update. Shorter feels more
    # responsive; too short spends more CPU re-decoding the same audio.
    update_interval: float = float(_env("EDITH_UPDATE_INTERVAL", "0.1"))
    # A pause ends a transcription line, so a moment's hesitation splits
    # one command into two. Lines are joined until the microphone has been
    # quiet this long. Too short and commands fragment; too long and every
    # command waits for it.
    aggregate_seconds: float = float(_env("EDITH_AGGREGATE", "0.6"))

    # --- turn taking -----------------------------------------------
    # Endpointing is the transcriber's job now, so there is no noise
    # threshold to tune here. What remains is how long to stay deaf after
    # replying, so EDITH does not hear the tail of its own voice.
    # Long enough for the tail of EDITH's own speech to clear the audio
    # pipeline before the microphone opens again. Too short and the last
    # syllable is transcribed as the next command.
    settle_seconds: float = float(_env("EDITH_SETTLE", "0.6"))


@dataclass(frozen=True)
class SearchConfig:
    # Accept either spelling so an existing .env keeps working.
    api_key: str = _env("TAVILY_API_KEY", _env("TAVILYAPIKEY", ""))
    max_results: int = int(_env("SEARCH_MAX_RESULTS", "5"))
    # 'advanced' reads more of each page and costs more credits.
    depth: str = _env("SEARCH_DEPTH", "basic")
    timeout: float = float(_env("SEARCH_TIMEOUT", "20"))
    # Characters of extracted text kept per result before it reaches the
    # model. Five full pages would overflow an 8192-token context.
    chars_per_result: int = int(_env("SEARCH_CHARS_PER_RESULT", "1200"))


@dataclass(frozen=True)
class RouterConfig:
    # Score at or above which a single candidate is executed without an LLM.
    confident_threshold: float = 0.92
    # A runner-up within this margin of the winner makes the request ambiguous.
    ambiguity_margin: float = 0.15
    # Below this, do not even offer a clarification -- escalate to the agent.
    floor_threshold: float = 0.55


@dataclass(frozen=True)
class Config:
    llm: LLMConfig = LLMConfig()
    router: RouterConfig = RouterConfig()
    search: SearchConfig = SearchConfig()
    voice: VoiceConfig = VoiceConfig()
    apps_cache_ttl_seconds: int = int(_env("APPS_CACHE_TTL", str(24 * 3600)))
    # Folders change more often than installed apps.
    folder_index_ttl_seconds: int = int(_env("FOLDER_INDEX_TTL", str(3600)))
    confirmation_ttl_seconds: float = float(_env("CONFIRMATION_TTL", "60"))
    # Moving a couple of files is routine; moving a folder's worth is not.
    confirm_move_threshold: int = int(_env("CONFIRM_MOVE_THRESHOLD", "3"))
    trace_enabled: bool = _env("EDITH_TRACE", "1") == "1"


CONFIG = Config()
