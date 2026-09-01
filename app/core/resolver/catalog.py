
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from app.core.resolver.apps_index import AppsIndex
from app.security.path_resolver import PathResolver, drive_reference

KIND_FOLDER = "folder"
KIND_FILE = "file"
KIND_SHELL = "shell"
KIND_SITE = "site"
KIND_APP = "app"

# Tie-breakers when the same word could plausibly mean two things.
# 'open downloads' is the folder, never a Store app called Downloads.
KIND_PRIORITY = {
    KIND_SHELL: 1.00,
    KIND_FOLDER: 1.00,
    KIND_FILE: 1.00,
    KIND_SITE: 0.99,
    KIND_APP: 0.98,
}

# 'report.pdf', 'notes.txt' -- an extension the user typed is an explicit
# signal that they mean a file, not an app or a website.
_FILENAME = re.compile(r"^[\w\-. ()\[\]]+\.[a-z0-9]{1,6}$", re.IGNORECASE)

SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "kalvium community": "https://kalvium.community",
    "kalvium": "https://kalvium.community",
    "stack overflow": "https://stackoverflow.com",
    "leetcode": "https://leetcode.com",
}

SHELL_LOCATIONS = {
    "recycle_bin": [
        "recycle bin", "recyclebin", "recycle", "bin",
        "trash", "trash bin", "the trash",
    ],
}

APP_ALIASES = {
    "vs code": "visual studio code",
    "vscode": "visual studio code",
    "code editor": "visual studio code",
    "my editor": "visual studio code",
    "file manager": "file explorer",
    "file browser": "file explorer",
    "explorer": "file explorer",
    "terminal": "windows terminal",
    "cmd": "windows terminal",
    "calculator": "calculator",

    # Browsers. The plain browser is Chrome; the private one is Brave.
    "browser": "chrome",
    "web browser": "chrome",
    "edge": "microsoft edge",

    "private browser": "brave",
    "private web browser": "brave",
    "private browsing": "brave",
    "private": "brave",
}

_DOMAIN = re.compile(
    r"^[a-z0-9][\w-]*(\.[\w-]+)+$",
    re.IGNORECASE,
)

_KNOWN_TLDS = {
    "com", "org", "net", "io", "dev", "ai", "co", "in",
    "app", "me", "gg", "community", "edu", "gov",
}


@dataclass
class Candidate:
    kind: str
    label: str
    tool: str
    arguments: dict
    score: float
    detail: dict = field(default_factory=dict)


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split())


def similarity(query: str, name: str) -> float:
    query_norm, name_norm = normalize(query), normalize(name)

    if not query_norm or not name_norm:
        return 0.0

    if query_norm == name_norm:
        return 1.0

    query_tokens = set(query_norm.split())
    name_tokens = set(name_norm.split())

    # Every word the user said appears in the app's name: strong signal.
    # 'brave' -> 'Brave Browser', 'vs code' -> 'Visual Studio Code'.
    if query_tokens and query_tokens <= name_tokens:
        return 0.90 + 0.05 * (len(query_tokens) / len(name_tokens))

    ratio = SequenceMatcher(None, query_norm, name_norm).ratio()

    if name_norm.startswith(query_norm) and len(query_norm) >= 3:
        ratio = max(ratio, 0.86)

    return ratio


class EntityCatalog:
    """
    Resolves a natural-language object ('brave', 'my downloads', 'youtube')
    to ranked, executable candidates. This -- not regex over the sentence --
    is what decides whether 'open X' means an app, a site, or a folder.
    """

    def __init__(self, apps_index: AppsIndex | None = None, folder_index=None):
        self.apps = apps_index or AppsIndex()
        self.paths = PathResolver()
        self.folders = folder_index

    def resolve(self, text: str, hint: str | None = None) -> list[Candidate]:
        query = normalize(text)

        if not query:
            return []

        candidates: list[Candidate] = []

        # An alias is a declared preference, not a guess: 'browser' means
        # Chrome even though folders named 'browser' exist on disk. Resolving
        # it here stops fuzzy matches from competing with an exact intent.
        alias_key = re.sub(r"^(my|the)\s+", "", query).strip()

        if hint in (None, KIND_APP) and alias_key in APP_ALIASES:
            candidates.extend(self._applications(alias_key))

        else:
            if hint in (None, KIND_SHELL):
                candidates.extend(self._shell(query))

            if hint in (None, KIND_FOLDER):
                candidates.extend(self._folders(text, query))

            if hint is None:
                candidates.extend(self._files(text))

            if hint in (None, KIND_SITE):
                candidates.extend(self._sites(text, query))

            if hint in (None, KIND_APP):
                candidates.extend(self._applications(query))

        for candidate in candidates:
            candidate.score *= KIND_PRIORITY[candidate.kind]

        candidates.sort(key=lambda item: item.score, reverse=True)

        return self._collapse_same_entity(candidates)

    # ------------------------------------------------------------------

    @staticmethod
    def _collapse_same_entity(candidates: list[Candidate]) -> list[Candidate]:
        """
        'youtube' matches both the site and the Store app. That is not real
        ambiguity -- it is one entity with two doors. Keep the higher-priority
        door so it does not trigger a pointless clarification question.
        """
        seen: set[tuple] = set()
        collapsed: list[Candidate] = []

        for candidate in candidates:
            # Two folders can legitimately share a name in different places;
            # they are distinct entities and must both survive to become a
            # clarification question.
            key = (
                normalize(candidate.label),
                candidate.arguments.get("path", ""),
            )

            if key in seen:
                continue

            seen.add(key)
            collapsed.append(candidate)

        return collapsed

    def _shell(self, query: str) -> list[Candidate]:
        for location, aliases in SHELL_LOCATIONS.items():
            best = max((similarity(query, alias) for alias in aliases), default=0.0)

            if best >= 0.85:
                return [Candidate(
                    kind=KIND_SHELL,
                    label="Recycle Bin",
                    tool="open_windows_shell",
                    arguments={"location": location},
                    score=best,
                )]

        return []

    def _folders(self, raw: str, query: str) -> list[Candidate]:
        stripped = re.sub(r"^(my|the)\s+", "", query)
        stripped = re.sub(r"\s+(folder|directory)$", "", stripped)

        results = []

        # 'open d drive', 'open d:', 'open d disk', 'open d'. Matched on the
        # raw text, because normalize() strips the colon that distinguishes
        # a drive from a one-letter name.
        drive = drive_reference(raw.strip())

        if drive is not None:
            return [Candidate(
                kind=KIND_FOLDER,
                label=f"{drive.drive[0]} drive",
                tool="open_folder",
                arguments={"path": str(drive)},
                score=1.0,
            )]

        for alias in self.paths.aliases:
            score = similarity(stripped, alias)

            if score >= 0.85:
                results.append(Candidate(
                    kind=KIND_FOLDER,
                    label=alias.capitalize(),
                    tool="open_folder",
                    arguments={"path": alias.capitalize()},
                    score=score,
                ))

        if results or self.folders is None:
            return results

        # Named folders anywhere on the working drive: 'open project folder'.
        #
        # Scored below a named app on purpose. Finding a folder by scanning
        # the disk is a weaker signal than the user naming a program, and
        # normalize() strips punctuation -- so the folder 'Notepad++' looks
        # exactly like the query 'notepad'. At 0.95 that turned 'open
        # notepad' into a clarification question instead of opening Notepad.
        for entry in self.folders.find(stripped, limit=4):
            results.append(Candidate(
                kind=KIND_FOLDER,
                label=entry["name"],
                tool="open_folder",
                arguments={"path": entry["path"]},
                score=0.80,
                detail={"path": entry["path"]},
            ))

        return results

    def _files(self, raw: str) -> list[Candidate]:
        token = raw.strip().strip('"')

        if not _FILENAME.match(token):
            return []

        suffix = token.rsplit(".", 1)[-1].lower()

        # 'kalvium.community' is a website, not a file.
        if suffix in _KNOWN_TLDS:
            return []

        return [Candidate(
            kind=KIND_FILE,
            label=token,
            tool="open_file",
            arguments={"path": token},
            score=0.95,
        )]

    def _sites(self, raw: str, query: str) -> list[Candidate]:
        token = raw.strip().lower().rstrip("/")

        # A literal domain the user typed or spoke: 'kalvium.community'.
        if _DOMAIN.match(token) and token.rsplit(".", 1)[-1] in _KNOWN_TLDS:
            return [Candidate(
                kind=KIND_SITE,
                label=token,
                tool="open_website",
                arguments={"url": token},
                score=1.0,
            )]

        results = []

        for name, url in SITES.items():
            score = similarity(query, name)

            if score >= 0.85:
                results.append(Candidate(
                    kind=KIND_SITE,
                    label=name,
                    tool="open_website",
                    arguments={"url": url},
                    score=score,
                ))

        return results

    def _applications(self, query: str) -> list[Candidate]:
        query = APP_ALIASES.get(query, query)

        results = []

        for entry in self.apps.entries():
            score = similarity(query, entry["name"])

            if score < 0.72:
                continue

            results.append(Candidate(
                kind=KIND_APP,
                label=entry["name"],
                tool="launch_application",
                arguments={"application": entry["name"]},
                score=score,
                detail={"kind": entry["kind"], "target": entry["target"]},
            ))

        results.sort(key=lambda item: item.score, reverse=True)

        return self._collapse_same_product(results)[:4]

    @staticmethod
    def _collapse_same_product(results: list[Candidate]) -> list[Candidate]:
        """
        Windows lists one product several ways: 'Chrome' as an executable and
        'Google Chrome' as a Store entry. Offering both as a choice is a
        question with no meaningful answer, so keep one.

        Same product means one name's words are contained in the other's.
        The executable wins, because a launch can be verified by process name.
        """
        kept: list[Candidate] = []

        for candidate in results:
            words = set(normalize(candidate.label).split())

            duplicate_of = None

            for index, existing in enumerate(kept):
                other = set(normalize(existing.label).split())

                if words <= other or other <= words:
                    duplicate_of = index
                    break

            if duplicate_of is None:
                kept.append(candidate)
                continue

            existing = kept[duplicate_of]

            if (
                existing.detail.get("kind") == "appid"
                and candidate.detail.get("kind") == "exe"
            ):
                # Keep the higher score so ranking against other kinds of
                # candidate is unaffected by which door we picked.
                candidate.score = max(candidate.score, existing.score)
                kept[duplicate_of] = candidate

        return kept
