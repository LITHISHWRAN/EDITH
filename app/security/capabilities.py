"""
Per-location capability matrix.

Every filesystem operation is checked against the zone that owns the path.
Zones are matched longest-prefix-first, and anything that matches no zone is
denied -- the default is no access, not full access.

Capabilities are directional. A location can be a legal *source* for a copy
without being writable, which is why COPY and MOVE are separate from WRITE.
"""

import os
from dataclasses import dataclass
from pathlib import Path

READ = "read"            # list what is inside
WRITE = "write"          # create files or folders here
COPY = "copy"            # may be the source of a copy
MOVE = "move"            # may be the source of a move (leaves this location)
DUPLICATE = "duplicate"  # copy an item beside itself, in place
DELETE = "delete"        # send to the Recycle Bin

ALL = frozenset({READ, WRITE, COPY, MOVE, DUPLICATE, DELETE})
NONE = frozenset()

# Human wording for refusals, so the assistant explains the rule rather than
# reciting a constant.
CAPABILITY_VERBS = {
    READ: "look inside",
    WRITE: "create things in",
    COPY: "copy from",
    MOVE: "move things out of",
    DUPLICATE: "duplicate things in",
    DELETE: "delete things in",
}


@dataclass(frozen=True)
class Zone:
    label: str
    path: Path
    capabilities: frozenset

    def grants(self, capability: str) -> bool:
        return capability in self.capabilities


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    error: str = ""
    zone: Zone | None = None


def _system_root(variable: str, fallback: str) -> Path:
    return Path(os.environ.get(variable, fallback))


class CapabilityMatrix:
    """
    The policy table. Tools ask it questions; it never touches the disk.
    """

    def __init__(self, resolver=None):
        if resolver is None:
            from app.security.path_resolver import PathResolver

            resolver = PathResolver()

        self.resolver = resolver

        system_drive = Path(os.environ.get("SystemDrive", "C:") + "\\")

        denied = [
            ("Windows", _system_root("WINDIR", r"C:\Windows")),
            ("Program Files", _system_root("PROGRAMFILES", r"C:\Program Files")),
            (
                "Program Files (x86)",
                _system_root("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            ),
            ("ProgramData", _system_root("PROGRAMDATA", r"C:\ProgramData")),
            ("Recycle Bin", system_drive / "$Recycle.Bin"),
            ("Recovery", system_drive / "Recovery"),
        ]

        zones = [
            Zone(label, self._safe_resolve(path), NONE)
            for label, path in denied
        ]

        # Personal folders: readable, and usable as a copy source, but never
        # written into. Resolved through PathResolver so OneDrive redirection
        # is covered.
        for alias in ("desktop", "documents", "pictures", "videos", "music"):
            target = resolver.aliases.get(alias)

            if target is None:
                continue

            zones.append(
                Zone(
                    alias.capitalize(),
                    self._safe_resolve(target),
                    frozenset({READ, COPY}),
                )
            )

        # Downloads is a staging area: readable, writable, and clearable, so
        # incoming files can be sorted into folders in place. DUPLICATE stays
        # off -- cluttering Downloads with copies is never the intent.
        downloads = resolver.aliases.get("downloads")

        if downloads is not None:
            zones.append(
                Zone(
                    "Downloads",
                    self._safe_resolve(downloads),
                    frozenset({READ, WRITE, COPY, MOVE, DELETE}),
                )
            )

        # The working drive.
        for letter in self._workspace_drives():
            zones.append(Zone(f"{letter}: drive", Path(f"{letter}:\\"), ALL))

        # Read-only carve-outs, applied last so they sit deeper than the
        # drive zone and win the longest-prefix match. EDITH's own source is
        # protected here: an assistant that can rewrite or delete itself can
        # silently disable its own safety rules.
        for label, path in self._protected_paths():
            zones.append(Zone(label, self._safe_resolve(path), frozenset({READ, COPY})))

        # Longest path first, so the most specific rule wins.
        self.zones = sorted(
            zones,
            key=lambda zone: len(zone.path.parts),
            reverse=True,
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _protected_paths() -> list[tuple[str, Path]]:
        """
        Locations that are readable but never modified.

        Defaults to EDITH's own installation directory. Add more with
        EDITH_PROTECTED_PATHS as a semicolon-separated list.
        """
        from app.config import ROOT

        paths = [("my own program files", ROOT)]

        configured = os.environ.get("EDITH_PROTECTED_PATHS", "").strip()

        for entry in configured.split(";"):
            entry = entry.strip()

            if entry:
                paths.append((Path(entry).name or entry, Path(entry)))

        return paths

    @staticmethod
    def _workspace_drives() -> list[str]:
        configured = os.environ.get("EDITH_WORKSPACE_DRIVES", "D").strip()

        return [
            letter.strip().rstrip(":").upper()
            for letter in configured.split(",")
            if letter.strip()
        ]

    @staticmethod
    def _safe_resolve(path: Path) -> Path:
        try:
            return Path(path).resolve()

        except (OSError, RuntimeError):
            return Path(path)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def zone_for(self, path: Path) -> Zone | None:
        resolved = self._safe_resolve(path)

        for zone in self.zones:
            if resolved == zone.path or zone.path in resolved.parents:
                return zone

        return None

    def is_zone_root(self, path: Path) -> bool:
        resolved = self._safe_resolve(path)

        return any(resolved == zone.path for zone in self.zones)

    def check(self, path: Path, capability: str) -> Verdict:
        """May `capability` be exercised at this location?"""
        zone = self.zone_for(path)

        if zone is None:
            return Verdict(
                False,
                f"{path} is outside the folders I'm allowed to touch.",
            )

        if not zone.grants(capability):
            verb = CAPABILITY_VERBS.get(capability, capability)

            if not zone.capabilities:
                return Verdict(
                    False,
                    f"{zone.label} is a protected system location.",
                    zone,
                )

            return Verdict(
                False,
                f"I can't {verb} {zone.label}.",
                zone,
            )

        return Verdict(True, zone=zone)

    def check_item(self, path: Path, capability: str) -> Verdict:
        """
        As check(), but for an individual file or folder that is about to be
        modified or removed. A zone root is never itself a valid target --
        'delete my downloads' clears the folder, it does not remove it.
        """
        if self.is_zone_root(path):
            zone = self.zone_for(path)

            return Verdict(
                False,
                (
                    f"I can work inside {zone.label}, but I won't "
                    f"modify the folder itself."
                ),
                zone,
            )

        return self.check(path, capability)

    def describe(self) -> list[dict]:
        """Used by tests and by the 'what can you access' explanation."""
        return [
            {
                "label": zone.label,
                "path": str(zone.path),
                "capabilities": sorted(zone.capabilities),
            }
            for zone in sorted(self.zones, key=lambda z: str(z.path))
        ]
