"""Machine-neutral path discovery helpers.

No hardcoded drive letters or usernames — everything is derived at runtime so a
release build works on any machine: English or Chinese Windows, OneDrive-backed
or local Documents, data on any drive, or a fully custom location supplied via an
environment override. All functions are read-only and touch nothing on disk.
"""
from __future__ import annotations

import os
import string
from pathlib import Path
from typing import List, Optional


def all_drive_roots() -> List[Path]:
    """Every existing logical drive root (``C:/``, ``D:/`` …) on Windows.

    Returns ``[]`` on non-Windows. Used so we never assume a chat client lives on
    C: or D: — the user may have relocated its data dir to any drive.
    """
    roots: List[Path] = []
    if os.name != "nt":
        return roots
    for letter in string.ascii_uppercase:
        d = Path(f"{letter}:/")
        try:
            if d.exists():
                roots.append(d)
        except OSError:
            pass
    return roots


def real_documents() -> Optional[Path]:
    """The user's real "Documents" folder, honoring Windows folder redirection.

    A user can move Documents to another drive or back it with OneDrive; this asks
    the Win32 known-folder API for the actual location rather than assuming
    ``~/Documents``. Read-only (does not create or modify anything). Returns
    ``None`` off-Windows or on any failure.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        CSIDL_PERSONAL = 5  # "My Documents"
        SHGFP_TYPE_CURRENT = 0
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        ctypes.windll.shell32.SHGetFolderPathW(  # type: ignore[attr-defined]
            None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf
        )
        if buf.value:
            p = Path(buf.value)
            return p if p.exists() else None
    except Exception:
        return None
    return None


def candidate_documents_roots() -> List[Path]:
    """Likely "Documents" locations across machine configurations, de-duplicated.

    Order: the real (possibly redirected) Documents first, then common home and
    OneDrive variants — English ``Documents`` and Chinese ``文档``.
    """
    out: List[Path] = []
    seen: set = set()

    def _add(p: Optional[Path]) -> None:
        if p is None:
            return
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            out.append(p)

    _add(real_documents())
    home = Path.home()
    _add(home / "Documents")
    _add(home / "OneDrive" / "Documents")
    _add(home / "OneDrive" / "文档")
    return out


# Directories a bounded search never descends into: the chat client's user-data
# folder is never inside these, and walking them wastes the whole time budget.
_SCAN_SKIP_NAMES = {
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "$recycle.bin",
    "system volume information",
    "recovery",
    "perflogs",
    "node_modules",
    ".git",
    "__pycache__",
    "appdata",
}

# Budget for one drive. The scan only has to find a directory whose name is
# ``xwechat_files``; it is not a general indexer, so it is capped rather than
# allowed to run unbounded on a machine with millions of files.
_SCAN_MAX_DIRS = 20000
_SCAN_MAX_DEPTH = 4
_SCAN_BUDGET_SECONDS = 4.0


def scan_for_wechat_data_root(name: str = "xwechat_files") -> Optional[Path]:
    """Find a WeChat user-data folder that lives at a custom path.

    The default locations are tried first by the caller, but a user is free to
    relocate ``xwechat_files`` anywhere (a second drive, a Chinese-named backup
    folder). Rather than make that user hand-write a path into an environment
    variable, this walks each drive root breadth-first to a shallow depth,
    skipping system and dependency trees, and stops at the first match.

    Returns ``None`` when nothing matches inside the budget. Read-only.
    """
    if os.name != "nt":
        return None

    import time
    from collections import deque

    target = name.lower()
    deadline = time.monotonic() + _SCAN_BUDGET_SECONDS

    for drive in all_drive_roots():
        queue: deque = deque([(drive, 0)])
        visited = 0
        while queue:
            if visited >= _SCAN_MAX_DIRS or time.monotonic() > deadline:
                break
            current, depth = queue.popleft()
            visited += 1
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            if not entry.is_dir(follow_symlinks=False):
                                continue
                        except OSError:
                            continue
                        lowered = entry.name.lower()
                        if lowered == target:
                            found = Path(entry.path)
                            # The data root holds per-account wxid_* folders;
                            # an empty match is some other program's folder.
                            try:
                                if any(
                                    child.is_dir()
                                    for child in found.iterdir()
                                ):
                                    return found
                            except OSError:
                                continue
                        if lowered in _SCAN_SKIP_NAMES or lowered.startswith("."):
                            continue
                        if depth + 1 <= _SCAN_MAX_DEPTH:
                            queue.append((Path(entry.path), depth + 1))
            except OSError:
                continue
    return None
