
import math
import subprocess
from pathlib import Path
from datetime import date

# =====================
# 設定
# =====================

LAT = 35.506600
LON = 137.816433333

ZOOMS = [20, 19, 18]
RADIUS = 80  # 中心から80m、160m四方

EXE = Path(__file__).resolve().parent / "GEHistoricalImagery" / "gdal" / "GEHistoricalImagery.exe"

SAVE_DIR = (
    Path(__file__).resolve().parent
    / "google_earth_history"
    / "images"
)

SAVE_DIR.mkdir(parents=True, exist_ok=True)

# 過去に取得した撮影日一覧
DATES = [
    "2009/09/08",
    "2011/11/07",
    "2011/11/26",
    "2014/06/15",
    "2017/04/03",
    "2017/09/02",
    "2017/11/26",
    "2018/03/28",
    "2018/05/22",
    "2018/08/30",
    "2019/01/19",
    "2020/01/30",
    "2020/02/10",
    "2020/02/27",
    "2023/04/04",
    "2023/04/27",
    "2024/10/11",
    "2025/04/26"
]

# =====================
# 撮影日を選択
# =====================

dates = [
    date.fromisoformat(d.replace("/", "-"))
    for d in DATES
]

latest = max(dates)

today = date.today()

target = today.replace(
    year=today.year - 15
)

# =====================
# 取得範囲
# =====================

dy = RADIUS / 111320

dx = RADIUS / (
    111320 * math.cos(math.radians(LAT))
)

lower = f"{LAT-dy},{LON-dx}"
upper = f"{LAT+dy},{LON+dx}"

# =====================
# ダウンロード
# =====================

def download(label, target_date):

    # 最新画像は新しい順
    # 過去画像は15年前に近い順
    if label == "latest":
        candidates = sorted(
            dates,
            reverse=True
        )
    else:
        candidates = sorted(
            dates,
            key=lambda d: abs(
                (d - target_date).days
            )
        )

    for zoom in ZOOMS:

        for day in candidates:

            filename = (
                f"{label}_"
                f"{day.isoformat()}_"
                f"z{zoom}_"
                f"{RADIUS*2}m.tif"
            )

            output = SAVE_DIR / filename

            print(
                f"\n試行: {day} "
                f"ズーム{zoom}",
                flush=True
            )

            cmd = [
                str(EXE),
                "download",
                "--provider=TM",
                f"--lower-left={lower}",
                f"--upper-right={upper}",
                f"--zoom={zoom}",
                f"--date={day.strftime('%Y/%m/%d')}",
                f"--output={output}",
                "--exact-date"
            ]

            try:

                result = subprocess.run(
                    cmd,
                    cwd=EXE.parent,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180
                )

                log = (
                    result.stdout
                    + "\n"
                    + result.stderr
                )

                # 取得できたタイル数を判定
                import re

                match = re.search(
                    r"(\d+)\s+out of\s+(\d+)\s+downloaded",
                    log
                )

                success = (
                    result.returncode == 0
                    and match is not None
                    and int(match.group(1)) > 0
                    and int(match.group(1))
                        == int(match.group(2))
                    and output.exists()
                    and output.stat().st_size > 100
                )

                if success:

                    print(
                        "\n保存成功:",
                        output
                    )

                    return output

                # 失敗したファイルは削除
                if output.exists():
                    output.unlink()

                if match:
                    print(
                        f"取得不可: "
                        f"{match.group(1)}/"
                        f"{match.group(2)}タイル"
                    )
                else:
                    print(
                        "取得不可:",
                        log[-250:]
                    )

            except subprocess.TimeoutExpired:

                print("タイムアウト")

                if output.exists():
                    output.unlink()

            except Exception as e:

                print("エラー:", e)

                if output.exists():
                    output.unlink()

    print(
        f"\n{label}: "
        "取得可能な画像がありません"
    )

    return None


# =====================
# 実行
# =====================

print("最新画像を検索")
new_file = download("latest", latest)

print("\n15年前に近い画像を検索")
old_file = download("15years_ago", target)

print("\n====================")
print("処理完了")
print("====================")

print("最新:", new_file)
print("過去:", old_file)
