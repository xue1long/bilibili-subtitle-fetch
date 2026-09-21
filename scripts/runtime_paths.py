"""Portable paths shared by the standalone scripts."""

import os
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent


def project_root() -> Path:
    override = os.environ.get("BILIBILI_SFETCH_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    for parent in (SCRIPTS_DIR,) + tuple(SCRIPTS_DIR.parents):
        if (parent / ".git").exists():
            return parent
    return SCRIPTS_DIR.parent


def default_output_dir() -> Path:
    return project_root() / "10_raw" / "01_B站视频转录"


def compile_db_path() -> Path:
    return project_root() / "scripts" / "compile_db.json"


def favorites_path() -> Path:
    return project_root() / "videos_fav.json"


def edge_profile_dir() -> Path:
    override = os.environ.get("BILIBILI_SFETCH_EDGE_PROFILE")
    if override:
        return Path(override).expanduser()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Microsoft" / "Edge" / "User Data"
    return Path.home() / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data"


def chrome_profile_dir() -> Path:
    override = os.environ.get("BILIBILI_SFETCH_CHROME_PROFILE")
    if override:
        return Path(override).expanduser()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Google" / "Chrome" / "User Data"
    return Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
