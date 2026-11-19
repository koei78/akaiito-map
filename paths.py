"""Portable locations for optional tools and local application data."""
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def data_dir():
    override = os.environ.get("AKAIITO_DATA_DIR")
    if override:
        return configured_path("AKAIITO_DATA_DIR", override)
    if os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "AerialComparison"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "akaiito-map"


def configured_path(name, default):
    value = os.environ.get(name)
    path = Path(value).expanduser() if value else Path(default).expanduser()
    return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def imagery_executable():
    override = os.environ.get("AKAIITO_IMAGERY_EXE")
    if override:
        return configured_path("AKAIITO_IMAGERY_EXE", override)
    folder = ROOT / "GEHistoricalImagery" / "gdal"
    for filename in ("GEHistoricalImagery.exe", "GEHistoricalImagery"):
        candidate = folder / filename
        if candidate.is_file():
            return candidate
    found = shutil.which("GEHistoricalImagery")
    return Path(found) if found else folder / ("GEHistoricalImagery.exe" if os.name == "nt" else "GEHistoricalImagery")


def codex_executable():
    override = os.environ.get("AKAIITO_CODEX_CLI")
    if override:
        return str(configured_path("AKAIITO_CODEX_CLI", override))
    found = shutil.which("codex")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates = list((Path(local) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe"))
        if candidates:
            return str(max(candidates, key=lambda path: path.stat().st_mtime))
    return None
