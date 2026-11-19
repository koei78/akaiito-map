# 航空写真の比較

## フォルダ構成

- `app.py`：Flask のルートと比較処理
- `pin_store.py`：ユーザー、ピン、履歴の SQLite 操作
- `detection_store.py`：比較で検出した新築候補の履歴をユーザー別に保存
- `paths.py`：OS や設置場所に依存しないパス・外部ツールの探索
- `templates/`：ログイン画面、通常の地図、Google 地図の HTML
- `static/`：ピン画面の CSS・JavaScript と画像
- `static/mobile-map.css`・`static/mobile-map.js`：スマホ用の検索欄と下部操作シート
- `static/map-theme.css`：地図・ピン・アカウント画面に共通する白とグレーの外観
- `test_pins.py`：ログイン、共有権限、テンプレートのテスト

## 別のパソコンで動かす

Python 3.10 以降をインストールし、プロジェクトのフォルダで次を実行します。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

macOS / Linux では `.venv/bin/python` を使用します。ブラウザで `http://127.0.0.1:5000` を開きます。`server_watch.py` も同じ Python 環境から起動してください。フォルダを移動しても、プログラム内の相対パスはプロジェクトの場所を基準に解決されます。

航空写真の取得には **GEHistoricalImagery の実行ファイル** が別途必要です。既定では `GEHistoricalImagery/gdal/` に置いた実行ファイルを探し、なければ `PATH` を探します。別の場所に置く場合は `AKAIITO_IMAGERY_EXE` に実行ファイルのパスを設定してください。Windows 用 `.exe` だけを macOS / Linux で実行することはできません。対応する実行ファイルがない環境では、地図と保存ピンは使えますが、航空写真の比較は使えません。

AI 判定には Codex CLI のインストールとログインが必要です。`PATH` に `codex` がなければ `AKAIITO_CODEX_CLI` で指定できます。Google 地図は任意です。Google Cloud で API キーを発行しただけではアプリから読み取れません。プロジェクトのターミナルで `python configure_google_maps.py` を実行し、表示されない入力欄にキーを入れてください。キーはリポジトリ外の `google_maps_key.txt`（Windows の既定はユーザーの `AppData/Local/AerialComparison/`）に保存されます。別の保存先を使う場合は `GOOGLE_MAPS_KEY_FILE` で指定できます。環境変数 `GOOGLE_MAPS_API_KEY` を使う場合は `GOOGLE_MAPS_QUOTA_VERIFIED=1` も必要です。キーの文字列をチャットや Git に入れないでください。

データの置き場所を変える場合は `AKAIITO_DB_PATH`（ユーザー・ピン DB）、`AKAIITO_DATA_DIR`（利用回数・GSI キャッシュ）、`PHOTO_IMAGES_DIR`（取得画像）、`PHOTO_OUTPUT_DIR`（比較結果）を指定できます。相対指定はプロジェクトのフォルダが基準です。`PORT` で待受ポートを変更できます。


## ログインと保存ピン

スマホではホーム画面から起動したときだけアプリを利用できます。通常のブラウザで開くと追加方法を案内します。Android の対応ブラウザでは「ホーム画面に追加する」ボタンが表示される場合があります。iPhone では共有メニューの「ホーム画面に追加」を使用してください。追加後は必ずホーム画面のアイコンから開きます。PWA の追加には HTTPS（または開発用の localhost）が必要です。ホーム画面には `static/app-icon-192.png` / `static/app-icon-512.png` の「新築マップ」ロゴが使われます。デスクトップのブラウザ表示はこの制限の対象外です。

`python app.py` で起動して `/register` からユーザーを作成します。ログイン後、地図右側の「ピンを追加」を押して地図をクリックすると、名前・メモ・共有設定を保存できます。自分のピンは通常表示され、「共有ピンも表示」を選ぶと、他のユーザーが共有を許可したピンも表示されます。ピンをクリックすると編集・削除ができます。他のユーザーのピンは閲覧のみです。

新築検出の履歴は保存ピンとは別です。比較が完了すると、そのユーザーの新築候補の外周・撮影日・判定理由を DB に保存します。デスクトップでは「検出履歴」、スマホでは右側の「履歴」から過去の結果を開き、地図に再表示できます。この機能の追加前に一時表示していた結果は履歴に残っていません。

スマホの検索欄の右にあるユーザーアイコンから、ユーザー名とパスワードの変更、削除済みを含む自分のピン履歴、ログアウトを行えます。ユーザー名とパスワードの変更には現在のパスワードが必要です。ピンの削除は確認後に地図から取り除き、履歴は残します。

ユーザー、ピン、履歴は `akaiito.sqlite3` に保存され、Git には含まれません。別の保存先にする場合は `AKAIITO_DB_PATH` を設定してください。複数端末で同じピンを使う場合は、全員が同じサーバーにアクセスしてください。SQLite ファイルを複数端末で直接同期して同時使用しないでください。

公開サーバーでは、再起動後もログインを維持するため、十分長いランダムな `AKAIITO_SECRET_KEY` を環境変数に設定してください。HTTPS で公開する場合は `AKAIITO_HTTPS=1` も設定してください。登録は現在、アクセスできる人なら誰でも行えます。

航空写真の比較で表示される赤い候補は一時的な結果です。残したい場所は保存ピンとして登録してください。`get.py` は設定された地域の画像を取得し、`compare.py` と `compare_precise.py` は取得画像を比較します。
