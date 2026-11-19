# 航空写真の比較

Python と OpenCV が必要です。インストールされていない場合は、次を実行します。

```powershell
python -m pip install opencv-python numpy
```

同じ場所を同じ範囲・サイズで撮影し、位置を合わせた写真を指定します。

```powershell
python get.py 古い写真.jpg 新しい写真.jpg
```

結果は `result.png` に保存され、変化した場所が赤く表示されます。
出力先や変化の検出感度も指定できます。

```powershell
python get.py 古い写真.jpg 新しい写真.jpg -o 結果.png --threshold 25
```

判定値を小さくすると、より小さな変化も赤く表示します。建物を自動判別する処理ではないため、車や影、季節の違いなども赤くなる場合があります。新築かどうかは結果を目視で確認してください。
