"""
Index of folder names across the readable zones.

Measured on this machine: a full walk of D: is ~1.4s cold for 8.7k folders,
so the index is built in the background at startup and cached. Looking up
'open project folder' is then a dict hit rather than a disk walk.
"""

import json
import os
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path

from app.config import CACHE_DIR, CONFIG
from app.security.capabilities import CapabilityMatrix, READ

CACHE_FILE = CACHE_DIR / "folder_index.json"

# Directories that are never what the user means, and that dominate the walk
# time if included.
SKIP_NAMES = {
    "node_modules", "__pycache__", "site-packages", "$recycle.bin",
    "system volume information", ".git", ".venv", "venv", "env",
    ".next", ".cache", ".gradle", ".idea", ".vscode", "dist-info",
    "appdata", "obj", "bin", "target",
}

MAX_DEPTH = 8
MAX_FOLDERS = 60_000


def _skip(name: str) -> bool:
    lowered = name.lower()

    return lowered in SKIP_NAMES or lowered.startswith("$")


class FolderIndex:

    def __init__(self, matrix: CapabilityMatrix | None = None):
        self.matrix = matrix or CapabilityMatrix()
        self._entries: list[dict] | None = None
        self._name_map: dict[str, list[dict]] | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------

    def warm_async(self):
        threading.Thread(target=self.entries, daemon=True).start()

    def entries(self, force_refresh: bool = False) -> list[dict]:
        with self._lock:
            if self._entries is not None and not force_refresh:
                return self._entries

            entries = None if force_refresh else self._read_cache()

            if entries is None:
                entries = self._build()
                self._write_cache(entries)

            self._entries = entries
            self._name_map = None
            return entries

    def find(self, name: str, limit: int = 10) -> list[dict]:
        """Folders whose name matches, best first."""
        from app.core.resolver.catalog import normalize

        query = normalize(name)

        if not query:
            return []

        # Exact name hit is the overwhelmingly common case, and fuzzy-scoring
        # 11k folders costs ~150ms -- far over the fast-path budget. Take the
        # dict lookup first and only fall back to scoring when it misses.
        exact = self._by_name().get(query)

        if exact:
            return sorted(exact, key=lambda entry: entry["depth"])[:limit]

        scored = []

        for entry in self.entries():
            candidate = entry.get("normalized") or normalize(entry["name"])

            # Cheap prefilter: a typo-level match always shares a first
            # character and a similar length.
            if candidate[:1] != query[:1] or abs(len(candidate) - len(query)) > 3:
                continue

            score = self._typo_score(query, candidate)

            if score >= 0.86:
                # Shallower paths are more likely to be the one meant.
                scored.append((score - entry["depth"] * 0.005, entry))

        scored.sort(key=lambda pair: pair[0], reverse=True)

        return [entry for _, entry in scored[:limit]]

    @staticmethod
    def _typo_score(query: str, candidate: str) -> float:
        """
        Deliberately stricter than the app matcher: no prefix bonus.

        Folder names are not brand names. A prefix bonus makes 'bin' match
        'binding' and buries the real answer in a clarification list, so the
        only fuzziness allowed here is a genuine typo.
        """
        return SequenceMatcher(None, query, candidate).ratio()

    # ------------------------------------------------------------------
    # Staying current
    # ------------------------------------------------------------------

    def sync(self, tool_name: str, result: dict):
        """
        Keep the index in step with folders EDITH just changed.

        Updated in place rather than invalidated: a full rebuild costs ~2.5s,
        which the next lookup would pay for. Without this, a folder created a
        moment ago is not findable by name until the cache expires.
        """
        if not isinstance(result, dict) or not result.get("success"):
            return

        if tool_name == "create_folder":
            self.add(result.get("path"))

        elif tool_name == "delete_files":
            if result.get("is_folder"):
                self.remove(result.get("path"))

        elif tool_name == "move_files" and result.get("is_folder"):
            self.remove(result.get("source"))
            self.add(Path(result["destination"]) / result["name"])

        elif tool_name == "rename_item":
            self.remove(Path(result["path"]).parent / result["old_name"])
            self.add(result.get("path"))

    def add(self, path) -> None:
        if not path:
            return

        target = Path(path)

        if not target.is_dir():
            return

        with self._lock:
            if self._entries is None:
                return

            from app.core.resolver.catalog import normalize

            key = str(target).lower()

            if any(entry["path"].lower() == key for entry in self._entries):
                return

            entry = {
                "name": target.name,
                "path": str(target),
                "depth": len(target.parts),
                "normalized": normalize(target.name),
            }

            self._entries.append(entry)

            if self._name_map is not None:
                self._name_map.setdefault(entry["normalized"], []).append(entry)

    def remove(self, path) -> None:
        if not path:
            return

        prefix = str(path).lower()

        with self._lock:
            if self._entries is None:
                return

            # Descendants go too -- deleting a folder removes everything
            # underneath it.
            self._entries = [
                entry
                for entry in self._entries
                if not (
                    entry["path"].lower() == prefix
                    or entry["path"].lower().startswith(prefix + "\\")
                )
            ]

            self._name_map = None

    def invalidate(self) -> None:
        with self._lock:
            self._entries = None
            self._name_map = None

    def _by_name(self) -> dict[str, list[dict]]:
        if self._name_map is None:
            from app.core.resolver.catalog import normalize

            mapping: dict[str, list[dict]] = {}

            for entry in self.entries():
                key = entry.get("normalized")

                if key is None:
                    key = normalize(entry["name"])
                    entry["normalized"] = key

                mapping.setdefault(key, []).append(entry)

            self._name_map = mapping

        return self._name_map

    # ------------------------------------------------------------------

    def _roots(self) -> list[Path]:
        return [
            zone.path
            for zone in self.matrix.zones
            if READ in zone.capabilities
        ]

    def _build(self) -> list[dict]:
        entries: list[dict] = []
        seen: set[str] = set()

        for root in self._roots():
            if not root.is_dir():
                continue

            base_depth = len(root.parts)

            for current, dirnames, _ in os.walk(str(root)):
                depth = len(Path(current).parts) - base_depth

                if depth >= MAX_DEPTH:
                    dirnames[:] = []
                    continue

                dirnames[:] = [d for d in dirnames if not _skip(d)]

                for dirname in dirnames:
                    full = os.path.join(current, dirname)
                    key = full.lower()

                    if key in seen:
                        continue

                    seen.add(key)

                    entries.append({
                        "name": dirname,
                        "path": full,
                        "depth": depth + 1,
                    })

                if len(entries) >= MAX_FOLDERS:
                    return entries

        return entries

    # ------------------------------------------------------------------

    def _read_cache(self) -> list[dict] | None:
        try:
            payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))

        except (OSError, json.JSONDecodeError):
            return None

        if time.time() - payload.get("built_at", 0) > CONFIG.folder_index_ttl_seconds:
            return None

        entries = payload.get("entries")

        return entries if isinstance(entries, list) and entries else None

    @staticmethod
    def _write_cache(entries: list[dict]):
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(
                json.dumps({"built_at": time.time(), "entries": entries}),
                encoding="utf-8",
            )

        except OSError:
            pass
