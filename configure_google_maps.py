"""Save a Google Maps API key outside the repository without echoing it."""
import os
from getpass import getpass
from pathlib import Path

from paths import configured_path, data_dir


def main():
    folder = Path.home() / "AppData" / "Local" / "AerialComparison" if os.name == "nt" else data_dir()
    target = configured_path("GOOGLE_MAPS_KEY_FILE", folder / "google_maps_key.txt")
    key = getpass("Google Maps API キーを入力（画面には表示されません）: ").strip()
    if not key or "\n" in key or "\r" in key:
        raise SystemExit("キーを保存できませんでした。値を確認してください。")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(key, encoding="utf-8")
    if os.name != "nt":
        target.chmod(0o600)
    print(f"キーを {target} に保存しました。アプリを再起動してください。")


if __name__ == "__main__":
    main()
