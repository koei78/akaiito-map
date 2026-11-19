"""Windows ログイン中、航空写真サーバーの停止を検知して起動する。"""
import subprocess
import os
import sys
import time
from pathlib import Path
from urllib.request import urlopen

BASE = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "5001"))


def running():
    try:
        with urlopen(f"http://127.0.0.1:{PORT}/health", timeout=3) as response:
            return response.status == 200
    except OSError:
        return False


while True:
    if not running():
        subprocess.Popen([sys.executable, "-X", "utf8", str(BASE / "app.py")],
                         cwd=BASE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        time.sleep(15)
    time.sleep(30)
