"""pytest 会话级环境修正。

有些环境把 ffmpeg 的副本误命名成 ``ffprobe`` 放在 PATH 前面（Windows 上
``C:\\Users\\<user>\\bin`` 常见），这个假 ffprobe 不认识 ``-show_entries`` /
``-select_streams``，依赖 ffprobe 探测的用例会误判成"文件没有音频轨"。

测试启动时扫一遍 PATH 上的 ffprobe，把第一个真正支持这些选项的提到最前面；
找不到可用版本就保持原样，不影响其余用例。
"""

import os
import subprocess


_FFPROBE_UNSUPPORTED = ("Unrecognized option", "Option not found")


def _supports_show_entries(executable: str) -> bool:
    """这个 ffprobe 是否认识 -show_entries。"""
    try:
        result = subprocess.run(
            [executable, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", os.devnull],
            capture_output=True,
        )
    except OSError:
        return False
    stderr = result.stderr.decode(errors="ignore")
    return not any(marker in stderr for marker in _FFPROBE_UNSUPPORTED)


def _find_usable_ffprobe() -> str | None:
    names = ("ffprobe.exe", "ffprobe") if os.name == "nt" else ("ffprobe",)
    seen = set()
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        for name in names:
            candidate = os.path.join(directory, name)
            key = os.path.normcase(os.path.abspath(candidate))
            if key in seen or not os.path.isfile(candidate):
                continue
            seen.add(key)
            if _supports_show_entries(candidate):
                return candidate
    return None


def _promote_ffprobe() -> None:
    executable = _find_usable_ffprobe()
    if not executable:
        return
    directory = os.path.dirname(executable)
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if parts and os.path.normcase(os.path.abspath(parts[0])) == os.path.normcase(os.path.abspath(directory)):
        return
    os.environ["PATH"] = os.pathsep.join([directory] + parts)


_promote_ffprobe()
