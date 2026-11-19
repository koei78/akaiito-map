"""Windows ログイン中、航空写真サーバーの停止を検知して起動する。"""
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

BASE = Path(__file__).resolve().parent


def running():
    try:
        with urlopen("http://127.0.0.1:5000/status", timeout=3) as response:
            return response.status == 200
    except OSError:
        return False


while True:
    if not running():
        subprocess.Popen([sys.executable.replace("pythonw.exe", "python.exe"), "-X", "utf8", str(BASE / "app.py")],
                         cwd=BASE, creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(15)
    time.sleep(30)
