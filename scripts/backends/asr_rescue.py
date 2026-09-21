from dataclasses import dataclass
from pathlib import Path


@dataclass
class AsrResult:
    status: str
    path: Path | None = None
    model: str = ""
    error: str = ""


def run(bvid: str, audio_dir: Path, output_path: Path, browser: str = "chrome", model_size: str = "small") -> AsrResult:
    """Run the reusable ASR rescue backend and normalize its result."""
    from subtitle_rescue import rescue_subtitle
    try:
        result = rescue_subtitle(
            bvid, audio_dir, output_path, browser=browser, model_size=model_size,
        )
        return AsrResult("success", path=result["path"], model=result["model"])
    except Exception as exc:
        return AsrResult("failed", model=model_size, error=str(exc))
