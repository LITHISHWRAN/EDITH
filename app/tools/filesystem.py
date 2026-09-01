import os
import re
import shutil
from fnmatch import fnmatch
from pathlib import Path

from app.core.resolver.folder_index import SKIP_NAMES
from app.tools.launch import spawn

from app.config import CONFIG
from app.tools.base import Tool
from app.security.capabilities import (
    COPY,
    DELETE,
    DUPLICATE,
    MOVE,
    READ,
    WRITE,
)
from app.security.path_guard import PathGuard
from app.security.path_resolver import PathResolver

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None


# Natural-language file groups, so 'move all my PDFs' and 'move the images'
# do not need the model to enumerate extensions.
CATEGORIES = {
    "pdf": {".pdf"},
    "pdfs": {".pdf"},
    "image": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".heic"},
    "images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".heic"},
    "photo": {".jpg", ".jpeg", ".png", ".heic"},
    "photos": {".jpg", ".jpeg", ".png", ".heic"},
    "video": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm"},
    "videos": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm"},
    "music": {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"},
    "audio": {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"},
    "document": {".doc", ".docx", ".txt", ".rtf", ".odt", ".pdf"},
    "documents": {".doc", ".docx", ".txt", ".rtf", ".odt", ".pdf"},
    "spreadsheet": {".xls", ".xlsx", ".csv", ".ods"},
    "spreadsheets": {".xls", ".xlsx", ".csv", ".ods"},
    "archive": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "installer": {".exe", ".msi"},
    "installers": {".exe", ".msi"},
    "code": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".html", ".css"},
    "python": {".py"},
}


# Opening one of these in Windows runs it. EDITH opens documents and media;
# starting programs goes through launch_application, which is policy-checked.
BLOCKED_OPEN_SUFFIXES = {
    ".exe", ".dll", ".msi", ".com", ".scr", ".sys", ".cpl",
    ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".wsf", ".wsh",
    ".hta", ".reg", ".lnk", ".pif", ".jar", ".msc", ".inf",
}


def _skip_dir(name: str) -> bool:
    lowered = name.lower()

    return lowered in SKIP_NAMES or lowered.startswith("$")


def _plural(count: int, noun: str = "item") -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _unique_name(target: Path) -> Path:
    """
    'resume.pdf' -> 'resume (2).pdf' when the name is taken.

    Copies and duplicates must never overwrite an existing file; silently
    replacing something the user already had is unrecoverable.
    """
    if not target.exists():
        return target

    stem, suffix = target.stem, target.suffix

    for index in range(2, 1000):
        candidate = target.with_name(f"{stem} ({index}){suffix}")

        if not candidate.exists():
            return candidate

    raise OSError(f"Could not find a free name beside {target}")


class _FileOperationTool(Tool):
    """Shared resolution, validation and matching for filesystem writes."""

    def __init__(self):
        self.path_guard = PathGuard()
        self.path_resolver = PathResolver()

    def _safe_path(
        self,
        path: str,
        mode: str = READ,
        base: Path | None = None,
        item: bool = False,
    ):
        """Returns (Path, None) or (None, error_dict)."""
        try:
            resolved = self.path_resolver.resolve(path, base=base)

        except (TypeError, ValueError) as error:
            return None, {"success": False, "error": str(error)}

        validation = self.path_guard.validate(
            str(resolved),
            mode=mode,
            item=item,
        )

        if not validation["allowed"]:
            return None, {"success": False, "error": validation["error"]}

        return Path(validation["path"]), None

    def _move_single(self, source_path: Path, destination_path: Path):
        """Move one named file or folder, keeping its name."""
        # check_item, not check: a zone root such as Downloads or D:\ is
        # never itself something that can be moved away.
        _, error = self._safe_path(str(source_path), mode=MOVE, item=True)

        if error:
            return error

        if source_path == destination_path or source_path in destination_path.parents:
            return {
                "success": False,
                "error": "I can't move a folder into itself.",
            }

        target = destination_path / source_path.name

        if target.exists():
            return {
                "success": False,
                "error": (
                    f"'{source_path.name}' already exists in "
                    f"{destination_path.name}."
                ),
            }

        try:
            shutil.move(str(source_path), str(target))

        except OSError as move_error:
            return {"success": False, "error": str(move_error)}

        if not target.exists() or source_path.exists():
            return {
                "success": False,
                "error": f"The move could not be confirmed for {source_path.name}.",
            }

        return {
            "success": True,
            "moved": 1,
            "failed": 0,
            "name": target.name,
            "is_folder": target.is_dir(),
            "source": str(source_path),
            "destination": str(destination_path),
        }

    @staticmethod
    def _match(
        directory: Path,
        pattern: str | None,
        category: str | None,
    ) -> list[Path]:
        extensions = CATEGORIES.get((category or "").strip().lower())

        matches = []

        for entry in directory.iterdir():
            if not entry.is_file():
                continue

            if extensions is not None:
                if entry.suffix.lower() in extensions:
                    matches.append(entry)

                continue

            if pattern:
                if entry.match(pattern):
                    matches.append(entry)

                continue

            matches.append(entry)

        return sorted(matches, key=lambda item: item.name.lower())


class CreateFolderTool(_FileOperationTool):

    name = "create_folder"

    description = (
        "Create a new folder. Use this when the user asks for a folder to "
        "be made."
    )

    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the new folder.",
            },
            "parent": {
                "type": "string",
                "description": (
                    "Folder to create it in. Use a logical name such as "
                    "Desktop, Documents or Downloads. Defaults to Home."
                ),
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    }

    def execute(self, name: str, parent: str = "Home"):
        name = (name or "").strip().strip('"')

        if not name:
            return {"success": False, "error": "No folder name was given."}

        if any(character in name for character in r'\/:*?"<>|'):
            return {
                "success": False,
                "error": f"'{name}' is not a valid Windows folder name.",
            }

        parent_path, error = self._safe_path(parent or "Home", mode=WRITE)

        if error:
            return error

        if not parent_path.is_dir():
            return {
                "success": False,
                "error": f"That folder does not exist: {parent_path}",
            }

        target, error = self._safe_path(str(parent_path / name), mode=WRITE)

        if error:
            return error

        if target.exists():
            return {
                "success": True,
                "path": str(target),
                "name": target.name,
                "already_existed": True,
            }

        try:
            target.mkdir(parents=False)

        except OSError as error:
            return {"success": False, "error": str(error)}

        # Verify rather than assume (requirement 17).
        if not target.is_dir():
            return {
                "success": False,
                "error": f"The folder was not created: {target}",
            }

        return {
            "success": True,
            "path": str(target),
            "name": target.name,
            "already_existed": False,
        }


class MoveFilesTool(_FileOperationTool):

    name = "move_files"

    risk = "destructive"

    description = (
        "Move files from one folder to another, optionally filtered by a "
        "category such as pdf, images, videos, music, documents, archives "
        "or code, or by a glob pattern such as *.txt."
    )

    parameters = {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Folder to move files from.",
            },
            "destination": {
                "type": "string",
                "description": "Folder to move files into.",
            },
            "category": {
                "type": "string",
                "description": (
                    "Optional file group: pdf, images, videos, music, "
                    "documents, spreadsheets, archives, installers, code."
                ),
            },
            "pattern": {
                "type": "string",
                "description": "Optional glob such as *.pdf.",
            },
        },
        "required": ["source", "destination"],
        "additionalProperties": False,
    }

    def execute(
        self,
        source: str,
        destination: str,
        category: str = "",
        pattern: str = "",
        _confirmed: bool = False,
    ):
        source_path, error = self._safe_path(source, mode=MOVE)

        if error:
            return error

        if not source_path.is_dir():
            return {
                "success": False,
                "error": f"That folder does not exist: {source_path}",
            }

        destination_path, error = self._safe_path(
            destination,
            mode=WRITE,
            base=source_path,
        )

        if error:
            return error

        if not destination_path.is_dir():
            return {
                "success": False,
                "error": (
                    f"The destination folder does not exist: "
                    f"{destination_path}. Create it first, or pass the "
                    f"full path returned when it was created."
                ),
            }

        if source_path == destination_path:
            return {
                "success": False,
                "error": "The source and destination are the same folder.",
            }

        # Same three cases as copy_files, so the two behave alike:
        #   a file            -> move that file
        #   a folder + filter -> move the matching files out of it
        #   a folder alone    -> move the folder itself
        if not source_path.is_dir() or (not category and not pattern):
            return self._move_single(source_path, destination_path)

        try:
            matches = self._match(source_path, pattern, category)

        except OSError as error:
            return {"success": False, "error": str(error)}

        if not matches:
            what = category or pattern or "files"

            return {
                # Nothing changed. Reporting success here invites the model
                # to describe a move that never happened.
                "success": False,
                "moved": 0,
                "nothing_matched": True,
                "error": (
                    f"I found no {what} in {source_path.name}, "
                    f"so nothing was moved."
                ),
            }

        # Real dry run: the count in the question is observed, not predicted.
        if not _confirmed and len(matches) > CONFIG.confirm_move_threshold:
            return {
                "success": False,
                "requires_confirmation": True,
                "summary": (
                    f"This will move {_plural(len(matches), 'file')} from "
                    f"{source_path.name} into {destination_path.name}."
                ),
                "preview": {
                    "count": len(matches),
                    "names": [item.name for item in matches[:5]],
                },
            }

        moved, failed = [], []

        for item in matches:
            target = destination_path / item.name

            if target.exists():
                failed.append({"name": item.name, "error": "already exists"})
                continue

            try:
                shutil.move(str(item), str(target))

            except OSError as move_error:
                failed.append({"name": item.name, "error": str(move_error)})
                continue

            # Verify each move actually landed.
            if target.exists() and not item.exists():
                moved.append(item.name)
            else:
                failed.append({"name": item.name, "error": "move not confirmed"})

        return {
            "success": True,
            "moved": len(moved),
            "failed": len(failed),
            "failures": failed[:5],
            "source": str(source_path),
            "destination": str(destination_path),
        }


class CopyFilesTool(_FileOperationTool):

    name = "copy_files"

    description = (
        "Copy files or a folder from one place to another, leaving the "
        "originals untouched. Use this for 'copy X and paste it in Y'."
    )

    parameters = {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": (
                    "Folder to copy from, or the full path of a single "
                    "file or folder to copy."
                ),
            },
            "destination": {
                "type": "string",
                "description": "Folder to copy into.",
            },
            "category": {
                "type": "string",
                "description": (
                    "Optional file group when copying from a folder: pdf, "
                    "images, videos, music, documents, archives, code."
                ),
            },
            "pattern": {
                "type": "string",
                "description": "Optional glob such as *.pdf.",
            },
        },
        "required": ["source", "destination"],
        "additionalProperties": False,
    }

    def execute(
        self,
        source: str,
        destination: str,
        category: str = "",
        pattern: str = "",
        _confirmed: bool = False,
    ):
        # COPY on the source, WRITE on the destination. That split is what
        # lets Documents be copied *from* while staying read-only.
        source_path, error = self._safe_path(source, mode=COPY)

        if error:
            return error

        if not source_path.exists():
            return {
                "success": False,
                "error": f"That does not exist: {source_path}",
            }

        destination_path, error = self._safe_path(
            destination,
            mode=WRITE,
            base=source_path if source_path.is_dir() else source_path.parent,
        )

        if error:
            return error

        if not destination_path.is_dir():
            return {
                "success": False,
                "error": (
                    f"The destination folder does not exist: "
                    f"{destination_path}. Create it first."
                ),
            }

        # Three unambiguous cases, in order:
        #   a file            -> copy that file
        #   a folder + filter -> copy the matching files out of it
        #   a folder alone    -> copy the folder itself, as a subfolder
        if not source_path.is_dir():
            return self._copy_single(source_path, destination_path)

        if not category and not pattern:
            return self._copy_single(source_path, destination_path)

        try:
            matches = self._match(source_path, pattern, category)

        except OSError as error:
            return {"success": False, "error": str(error)}

        if not matches:
            what = category or pattern or "files"

            return {
                "success": True,
                "copied": 0,
                "message": f"There were no {what} in {source_path.name}.",
            }

        if not _confirmed and len(matches) > CONFIG.confirm_move_threshold:
            return {
                "success": False,
                "requires_confirmation": True,
                "summary": (
                    f"This will copy {_plural(len(matches), 'file')} from "
                    f"{source_path.name} into {destination_path.name}."
                ),
                "preview": {
                    "count": len(matches),
                    "names": [item.name for item in matches[:5]],
                },
            }

        copied, failed = [], []

        for item in matches:
            target = _unique_name(destination_path / item.name)

            try:
                shutil.copy2(str(item), str(target))

            except OSError as copy_error:
                failed.append({"name": item.name, "error": str(copy_error)})
                continue

            if target.exists():
                copied.append(target.name)
            else:
                failed.append({"name": item.name, "error": "copy not confirmed"})

        return {
            "success": True,
            "copied": len(copied),
            "failed": len(failed),
            "failures": failed[:5],
            "source": str(source_path),
            "destination": str(destination_path),
        }

    @staticmethod
    def _copy_single(source_path: Path, destination_path: Path):
        """Copy one named file or folder, keeping the original."""
        target = _unique_name(destination_path / source_path.name)

        try:
            if source_path.is_dir():
                shutil.copytree(str(source_path), str(target))
            else:
                shutil.copy2(str(source_path), str(target))

        except OSError as error:
            return {"success": False, "error": str(error)}

        if not target.exists():
            return {
                "success": False,
                "error": f"The copy did not appear at {target}.",
            }

        return {
            "success": True,
            "copied": 1,
            "failed": 0,
            "name": target.name,
            "source": str(source_path),
            "destination": str(destination_path),
        }


class DuplicateItemTool(_FileOperationTool):

    name = "duplicate_item"

    description = (
        "Duplicate a file or folder in place, beside the original. Use this "
        "for 'duplicate this resume' -- the copy is created in the same "
        "folder with a numbered name."
    )

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Full path of the file or folder to duplicate.",
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def execute(self, path: str):
        # Duplicating writes into the folder it reads from, so the location
        # needs the DUPLICATE capability -- read-only folders cannot.
        source, error = self._safe_path(path, mode=DUPLICATE)

        if error:
            return error

        if not source.exists():
            return {"success": False, "error": f"Not found: {source}"}

        target = _unique_name(source)

        try:
            if source.is_dir():
                shutil.copytree(str(source), str(target))
            else:
                shutil.copy2(str(source), str(target))

        except OSError as copy_error:
            return {"success": False, "error": str(copy_error)}

        if not target.exists():
            return {
                "success": False,
                "error": f"The duplicate did not appear at {target}.",
            }

        return {
            "success": True,
            "original": source.name,
            "name": target.name,
            "path": str(target),
        }


class DeleteFilesTool(_FileOperationTool):

    name = "delete_files"

    risk = "destructive"

    description = (
        "Move things to the Windows Recycle Bin. Deletion is always "
        "recoverable.\n"
        "To delete ONE file or folder, set 'path' to its full path -- for "
        "'delete the empty folder in Downloads', path is Downloads/empty.\n"
        "To delete SEVERAL files out of a folder, set 'folder' and also a "
        "'category' or 'pattern'. Never set 'folder' on its own."
    )

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Full path of the single file or folder to delete, "
                    "including the folder it lives in."
                ),
            },
            "folder": {
                "type": "string",
                "description": (
                    "Folder to delete matching files out of. Requires "
                    "category or pattern. This folder is not itself deleted."
                ),
            },
            "category": {
                "type": "string",
                "description": (
                    "File group to delete from 'folder': pdf, images, "
                    "videos, music, documents, spreadsheets, archives, "
                    "installers, code."
                ),
            },
            "pattern": {
                "type": "string",
                "description": (
                    "Glob to delete from 'folder', such as *.tmp. Use * to "
                    "mean every file in that folder."
                ),
            },
        },
        "required": [],
        "additionalProperties": False,
    }

    def execute(
        self,
        path: str = "",
        folder: str = "",
        category: str = "",
        pattern: str = "",
        target: str = "",
        _confirmed: bool = False,
    ):
        if send2trash is None:
            return {
                "success": False,
                "error": (
                    "Deletion is unavailable: the send2trash package is "
                    "not installed. I will not delete files permanently."
                ),
            }

        path = (path or target or "").strip()
        folder = (folder or "").strip()
        has_filter = bool(category or pattern)

        # 'path' and 'folder' are deliberately separate. When one parameter
        # meant both, 'delete the empty folder in Downloads' was read as
        # 'delete files in Downloads' and cleared the whole folder.
        if path and folder and not has_filter:
            folder = ""

        if not path and not folder:
            return {
                "success": False,
                "error": "Tell me which file or folder to delete.",
            }

        if path:
            target_path, error = self._safe_path(path, mode=DELETE)

            if error:
                return error

            if not target_path.exists():
                return {
                    "success": False,
                    "error": f"That does not exist: {target_path}",
                }

            return self._delete_single(target_path, _confirmed)

        # Bulk delete out of a folder.
        if not has_filter:
            return {
                "success": False,
                "error": (
                    f"I won't empty {Path(folder).name} without knowing "
                    f"which files you mean. Name a file type, or say every "
                    f"file, or give the full path of the one thing to delete."
                ),
            }

        target_path, error = self._safe_path(folder, mode=DELETE)

        if error:
            return error

        if not target_path.is_dir():
            return {
                "success": False,
                "error": f"That folder does not exist: {target_path}",
            }

        try:
            matches = self._match(target_path, pattern, category)

        except OSError as error:
            return {"success": False, "error": str(error)}

        if not matches:
            what = category or pattern or "files"

            return {
                # Nothing was removed, so this is not a success.
                "success": False,
                "deleted": 0,
                "nothing_matched": True,
                "error": (
                    f"I found no {what} in {target_path.name}, "
                    f"so nothing was deleted."
                ),
            }

        # Re-checked as items, so a zone root can never be the thing removed.
        for item in matches:
            _, error = self._safe_path(str(item), mode=DELETE, item=True)

            if error:
                return error

        if not _confirmed:
            return {
                "success": False,
                "requires_confirmation": True,
                "summary": (
                    f"This will move {_plural(len(matches), 'file')} from "
                    f"{target_path.name} to the Recycle Bin."
                ),
                "preview": {
                    "count": len(matches),
                    "names": [item.name for item in matches[:5]],
                },
            }

        deleted, failed = [], []

        for item in matches:
            try:
                send2trash(str(item))

            except Exception as delete_error:
                failed.append({"name": item.name, "error": str(delete_error)})
                continue

            if item.exists():
                failed.append({"name": item.name, "error": "still present"})
            else:
                deleted.append(item.name)

        return {
            "success": True,
            "deleted": len(deleted),
            "failed": len(failed),
            "failures": failed[:5],
            "folder": str(target_path),
            "recoverable": True,
        }

    def _delete_single(self, target_path: Path, confirmed: bool):
        """Delete one named file or folder, contents included."""
        # check_item, not check: Downloads and D:\ can be cleared out, but
        # the folders themselves are never the thing removed.
        _, error = self._safe_path(str(target_path), mode=DELETE, item=True)

        if error:
            return error

        is_folder = target_path.is_dir()

        if not confirmed:
            contents = 0

            if is_folder:
                try:
                    contents = sum(1 for _ in target_path.rglob("*"))
                except OSError:
                    contents = 0

            if is_folder and contents:
                summary = (
                    f"This will move the {target_path.name} folder and "
                    f"{_plural(contents)} inside it to the Recycle Bin."
                )
            elif is_folder:
                summary = (
                    f"This will move the empty folder {target_path.name} "
                    f"to the Recycle Bin."
                )
            else:
                summary = (
                    f"This will move {target_path.name} to the Recycle Bin."
                )

            return {
                "success": False,
                "requires_confirmation": True,
                "summary": summary,
                "preview": {"count": 1, "names": [target_path.name]},
            }

        try:
            send2trash(str(target_path))

        except Exception as delete_error:
            return {"success": False, "error": str(delete_error)}

        if target_path.exists():
            return {
                "success": False,
                "error": f"{target_path.name} is still there after deleting.",
            }

        return {
            "success": True,
            "deleted": 1,
            "failed": 0,
            "name": target_path.name,
            "path": str(target_path),
            "is_folder": is_folder,
            "recoverable": True,
        }


class RenameItemTool(_FileOperationTool):

    name = "rename_item"

    risk = "sensitive"

    description = "Rename a file or folder."

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Current path of the file or folder.",
            },
            "new_name": {
                "type": "string",
                "description": "New name, without any folder path.",
            },
        },
        "required": ["path", "new_name"],
        "additionalProperties": False,
    }

    def execute(self, path: str, new_name: str):
        new_name = (new_name or "").strip().strip('"')

        if not new_name or any(c in new_name for c in r'\/:*?"<>|'):
            return {
                "success": False,
                "error": f"'{new_name}' is not a valid Windows name.",
            }

        source, error = self._safe_path(path, mode=WRITE, item=True)

        if error:
            return error

        if not source.exists():
            return {"success": False, "error": f"Not found: {source}"}

        target, error = self._safe_path(
            str(source.parent / new_name),
            mode=WRITE,
            item=True,
        )

        if error:
            return error

        if target.exists():
            return {
                "success": False,
                "error": f"Something called '{new_name}' is already there.",
            }

        try:
            source.rename(target)

        except OSError as rename_error:
            return {"success": False, "error": str(rename_error)}

        return {
            "success": True,
            "old_name": source.name,
            "new_name": target.name,
            "path": str(target),
        }


class FindFilesTool(_FileOperationTool):

    name = "find_files"

    description = (
        "Search for files by extension, name fragment or glob across the "
        "readable drives. Use this for 'list all my .py files' or 'find my "
        "resume'. Searches D: by default."
    )

    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Extension such as .py, a glob such as *.pdf, or part "
                    "of a file name."
                ),
            },
            "folder": {
                "type": "string",
                "description": (
                    "Optional folder to search in. Defaults to the whole "
                    "working drive."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results to return. Default 100.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def execute(self, query: str, folder: str = "", limit: int = 100):
        query = (query or "").strip()

        if not query:
            return {"success": False, "error": "Nothing to search for."}

        limit = max(1, min(int(limit or 100), 1000))

        if folder:
            root, error = self._safe_path(folder, mode=READ)

            if error:
                return error

            if not root.is_dir():
                return {
                    "success": False,
                    "error": f"That folder does not exist: {root}",
                }

            roots = [root]

        else:
            readable = [
                zone.path
                for zone in self.path_guard.matrix.zones
                if READ in zone.capabilities
            ]

            def is_drive_root(path: Path) -> bool:
                return path == Path(path.drive + "\\")

            # Personal folders first. They are small, and they are where a
            # query like 'find my resume' usually belongs -- searching the
            # whole drive first could fill the result limit before reaching
            # Downloads at all.
            roots = [p for p in readable if not is_drive_root(p)]
            roots += [p for p in readable if is_drive_root(p)]

            # A zone nested inside another zone (D:\EDITH under D:\) would
            # otherwise be walked twice.
            roots = [
                path
                for path in roots
                if not any(other in path.parents for other in roots)
            ]

            roots = roots or [Path("D:\\")]

        matcher = self._matcher(query)

        found, truncated = [], False

        for root in roots:
            if not root.is_dir():
                continue

            for current, dirnames, filenames in os.walk(str(root)):
                dirnames[:] = [d for d in dirnames if not _skip_dir(d)]

                for filename in filenames:
                    if not matcher(filename):
                        continue

                    full = Path(current) / filename

                    try:
                        size = full.stat().st_size
                    except OSError:
                        size = None

                    found.append({
                        "name": filename,
                        "path": str(full),
                        "size": size,
                    })

                    if len(found) >= limit:
                        truncated = True
                        break

                if truncated:
                    break

            if truncated:
                break

        return {
            "success": True,
            "query": query,
            "count": len(found),
            # Say so explicitly -- a silently capped list reads as complete.
            "truncated": truncated,
            "results": found,
            "searched": [str(root) for root in roots],
        }

    @staticmethod
    def _matcher(query: str):
        lowered = query.lower().strip()

        if lowered.startswith("."):
            return lambda name: name.lower().endswith(lowered)

        if "*" in lowered or "?" in lowered:
            return lambda name: fnmatch(name.lower(), lowered)

        return lambda name: lowered in name.lower()


class OpenFileTool(_FileOperationTool):

    name = "open_file"

    description = (
        "Show a file to the user by opening it in whichever application "
        "Windows uses for it -- a PDF in the PDF reader, an image in the "
        "viewer. Only for when the user wants the file displayed on screen. "
        "If they are asking what a file says or contains, use read_file "
        "instead; this tool does not return any content."
    )

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Full path of the file, or part of its name such as "
                    "'itemized' or 'resume.pdf'."
                ),
            },
            "folder": {
                "type": "string",
                "description": (
                    "Optional folder to look in, such as Downloads."
                ),
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def execute(self, path: str, folder: str = ""):
        path = (path or "").strip().strip('"')

        if not path:
            return {"success": False, "error": "Tell me which file to open."}

        # An exact path is the easy case.
        direct, error = self._safe_path(path, mode=READ)

        if error:
            # A written-out path that policy refuses must say so. Falling
            # through to the name search would report 'I couldn't find it',
            # which is a different and untrue explanation.
            if Path(path).is_absolute() or "/" in path or "\\" in path:
                return error

        else:
            if direct.is_file():
                return self._open(direct)

            if direct.is_dir():
                return {
                    "success": False,
                    "error": (
                        f"{direct.name} is a folder, not a file. "
                        f"I can open the folder instead."
                    ),
                }

        matches = self._search(path, folder)

        if isinstance(matches, dict):
            return matches

        if not matches:
            where = f" in {folder}" if folder else ""

            return {
                "success": False,
                "error": f"I couldn't find a file matching '{path}'{where}.",
            }

        if len(matches) > 1:
            return {
                "success": False,
                "ambiguous": True,
                "query": path,
                "matches": [
                    {"name": item.name, "path": str(item)}
                    for item in matches[:5]
                ],
                "error": f"I found {len(matches)} files matching '{path}'.",
            }

        return self._open(matches[0])

    def _search(self, needle: str, folder: str):
        """Returns a list of Paths, or an error dict."""
        finder = FindFilesTool()
        needle = needle.strip()

        found = finder.execute(query=needle, folder=folder, limit=25)

        if not found.get("success"):
            return found

        results = [Path(item["path"]) for item in found["results"]]

        # The model habitually invents a filename from a description --
        # 'the aarogyaid pdf' becomes 'aarogyaid.pdf', which matches nothing
        # because the real file is 'aarogyaid AI api guide.pdf'. Retry on
        # the stem, keeping only files with the extension that was asked for.
        if not results and "." in needle:
            stem, _, suffix = needle.rpartition(".")

            if stem and suffix.isalnum():
                retry = finder.execute(query=stem, folder=folder, limit=25)

                if retry.get("success"):
                    results = [
                        Path(item["path"])
                        for item in retry["results"]
                        if item["name"].lower().endswith(f".{suffix.lower()}")
                    ]

        # The model often guesses a folder the user never named -- 'open the
        # itemized pdf' became a search of Documents. Widen rather than
        # report a file missing when it exists somewhere readable.
        if not results and folder:
            widened = self._search(needle, "")

            if not isinstance(widened, dict):
                results = widened

        # An exact file-name match beats a substring one: searching for
        # 'report.pdf' should not be ambiguous just because
        # 'report.pdf.backup' also exists.
        exact = [p for p in results if p.name.lower() == needle.lower()]

        return exact or results

    def _open(self, target: Path):
        # Windows runs a file's associated program when it is opened, so a
        # .bat or .exe would execute rather than being displayed. EDITH
        # opens documents and media; launching programs is a separate,
        # policy-checked tool.
        if target.suffix.lower() in BLOCKED_OPEN_SUFFIXES:
            return {
                "success": False,
                "error": (
                    f"I won't open {target.name} -- {target.suffix} files "
                    f"run when opened. Ask me to launch an application "
                    f"instead."
                ),
            }

        _, error = self._safe_path(str(target), mode=READ)

        if error:
            return error

        try:
            if hasattr(os, "startfile"):
                os.startfile(str(target))
            else:
                spawn(["xdg-open", str(target)])

        except OSError as open_error:
            return {
                "success": False,
                "error": (
                    f"Windows could not open {target.name}: {open_error}"
                ),
            }

        return {
            "success": True,
            "path": str(target),
            "name": target.name,
            "folder": str(target.parent),
        }


class OpenFolderTool(Tool):

    name = "open_folder"

    description = (
        "Open a folder in Windows File Explorer. Use this when the user "
        "wants to see a folder, not list its contents as text."
    )

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Folder to open. Use a logical name such as Downloads, "
                    "Documents, Desktop, Pictures, Videos, Music or Home, "
                    "or an absolute path."
                ),
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(self, folder_index=None):
        self.path_guard = PathGuard()
        self.path_resolver = PathResolver()

        if folder_index is None:
            from app.core.resolver.folder_index import FolderIndex

            folder_index = FolderIndex()

        self.folder_index = folder_index

    def execute(self, path: str):
        try:
            resolved = self.path_resolver.resolve(path)

        except (TypeError, ValueError) as error:
            return {"success": False, "error": str(error)}

        # A bare name like 'project' is not a path -- look it up by name
        # before treating it as one.
        if not resolved.is_dir() and not Path(path).is_absolute():
            found = self._search(path)

            if found is not None:
                return found

        validation = self.path_guard.validate(str(resolved))

        if not validation["allowed"]:
            return {"success": False, "error": validation["error"]}

        target = Path(validation["path"])

        if not target.is_dir():
            return {
                "success": False,
                "error": f"That folder does not exist: {target}",
            }

        return self._open(target)

    def _search(self, name: str):
        """Returns a result dict, or None to fall through to path handling."""
        cleaned = re.sub(
            r"^(?:my|the)\s+|\s+(?:folder|directory)$",
            "",
            name.strip(),
            flags=re.IGNORECASE,
        ).strip()

        matches = self.folder_index.find(cleaned or name)

        if not matches:
            return None

        exact = [
            match
            for match in matches
            if match["name"].lower() == (cleaned or name).lower()
        ]

        candidates = exact or matches

        if len(candidates) > 1:
            # Several real folders share the name. Ask rather than guess --
            # opening the wrong project folder is a silent wrong answer.
            return {
                "success": False,
                "ambiguous": True,
                "query": cleaned or name,
                "matches": [
                    {"name": item["name"], "path": item["path"]}
                    for item in candidates[:5]
                ],
                "error": (
                    f"I found {len(candidates)} folders called "
                    f"'{cleaned or name}'."
                ),
            }

        target = Path(candidates[0]["path"])

        validation = self.path_guard.validate(str(target))

        if not validation["allowed"]:
            return {"success": False, "error": validation["error"]}

        return self._open(target, matched=True)

    @staticmethod
    def _open(target: Path, matched: bool = False):
        try:
            spawn(["explorer.exe", str(target)])

        except OSError as error:
            return {"success": False, "error": str(error)}

        return {
            "success": True,
            "path": str(target),
            "name": target.name or str(target),
            "matched_by_name": matched,
        }


class ListDirectoryTool(Tool):

    name = "list_directory"

    description = (
        "List the files and directories inside a Windows directory. "
        "Use this when the user asks what files or folders are present "
        "in a directory."
    )

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Absolute path of the directory to inspect."
                ),
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(self):

        self.path_guard = PathGuard()
        self.path_resolver = PathResolver()

    def execute(self, path: str):

        # Resolve human-friendly paths
        resolved_path = self.path_resolver.resolve(path)

        # PathGuard expects a string
        validation = self.path_guard.validate(
            str(resolved_path)
        )

        if not validation["allowed"]:
            return {
                "success": False,
                "error": validation["error"],
            }

        safe_path = Path(
            validation["path"]
        )

        # -----------------------------------------
        # 2. Make sure it is a directory
        # -----------------------------------------

        if not safe_path.exists():

            return {
                "success": False,
                "error": (
                    f"Directory does not exist: "
                    f"{safe_path}"
                ),
            }

        if not safe_path.is_dir():

            return {
                "success": False,
                "error": (
                    f"Path is not a directory: "
                    f"{safe_path}"
                ),
            }

        # -----------------------------------------
        # 3. Read directory
        # -----------------------------------------

        try:

            entries = []

            for entry in safe_path.iterdir():

                try:

                    if entry.is_dir():

                        entries.append({
                            "name": entry.name,
                            "type": "directory",
                        })

                    else:

                        entries.append({
                            "name": entry.name,
                            "type": "file",
                            "size": entry.stat().st_size,
                        })

                except OSError:
                    # Ignore entries that cannot be inspected.
                    continue

            # Sort directories first, then files
            entries.sort(
                key=lambda item: (
                    item["type"] != "directory",
                    item["name"].lower(),
                )
            )

            return {
                "success": True,
                "path": str(safe_path),
                "count": len(entries),
                "entries": entries,
            }

        except PermissionError:

            return {
                "success": False,
                "error": (
                    f"Permission denied: "
                    f"{safe_path}"
                ),
            }

        except OSError as e:

            return {
                "success": False,
                "error": str(e),
            }