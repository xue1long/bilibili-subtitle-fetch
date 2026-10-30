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


def raw_dir() -> Path:
    """产物根目录：字幕、转录、笔记等落盘产物的顶层目录。

    默认在仓库内的 `10_raw/`。跨机共享时用 `BILIBILI_SFETCH_RAW_DIR` 指向同步盘；
    该值可以写**未展开**的 `%OneDriveConsumer%\\...`，由每台机器展开成自己的盘符，
    这样同一份配置字符串在所有机器上都成立（见 docs/adr/0001）。
    """
    override = os.environ.get("BILIBILI_SFETCH_RAW_DIR")
    if override:
        expanded = os.path.expandvars(override)
        if "%" in expanded:
            raise RuntimeError(
                f"BILIBILI_SFETCH_RAW_DIR 未展开，缺少被引用的环境变量: {override}"
            )
        return Path(expanded).expanduser()
    return project_root() / "10_raw"


def default_output_dir() -> Path:
    return raw_dir() / "01_B站视频转录"


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
    return project_root() / ".chrome-bilibili"
