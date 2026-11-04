# gq-filter: PDALベースの点群フィルタリングツール

`gq-filter` は、[PDAL](https://pdal.io/) を使用して、測量業務（地上レーザー・UAVレーザー）で頻繁に利用されるフィルタリング処理を自動化・簡略化するためのコマンドラインツールです。国土地理院の各種マニュアル等に準拠したプリセットを搭載しています。

## 主な機能
- **自動バッチ処理**: ディレクトリ内の全ファイル（.e57, .las, .laz）を順次処理。
- **一括マージ**: 複数ファイルを1つに結合し、同時に重複点除去を自動実行。
- **測量プリセット**: 地上レーザー(TLS)・UAVレーザーの規定値（入射角、距離、ボクセル等）をワンタップで適用。
- **自動器械点取得**: E57構造化ファイルからスキャナ位置を自動抽出し、距離フィルタを適用。
- **色クリーニング**: オルソ画像参照によるゴースト除去、または輝度ベースの簡易除去。
- **自動レポート**: 適用設定と処理点数を `processing_report.txt` に自動記録。
- **マルチコア並列処理による爆速バッチ処理**
  フォルダ内の複数の点群ファイルを自動認識し、CPUコアをフル活用して同時にフィルタリングを実行します。
- **真の入射角（掠射角）フィルター**
  単なる角度ではなく、点群から法線ベクトルを計算し、レーザーの入射軌跡との内積から正確な掠射角（デフォルト4度以上）を担保してノイズを除去します。
- **E57メタデータの自動解析**
  構造化E57ファイルのヘッダー情報からスキャナーの器械点（Pose）を瞬時に抽出し、15mなどの厳密な距離制限フィルターを自動適用します。
- **ミリ単位の精度維持**
  LAS出力時のスケールを自動で `0.001` に制御し、座標の丸め込み（1cmグリッドへの吸着現象）を完全に防ぎます。
- **外部プリセットによる柔軟なカスタマイズ**
  環境や機材に応じた設定（`presets.json`）をユーザーが自由に書き換えることができます。
## セットアップ (Windows)

1. **Python 3.10+** をインストール。
2. **Conda/Miniconda** 環境（PDALインストール済み）を用意。
3. `setup_windows.bat` を実行。
    - 仮想環境の作成と、システム上の PDAL DLL の自動探索・設定が行われます。

環境の有効化：
```powershell
.venv\Scripts\activate
```

## 使い方

もっともシンプルな実行（`01_raw` 内のファイルを TLS 規定値で個別処理し、`04_product` に保存）：
```powershell
python main.py --preset tls
```

すべてのファイルを1つに結合して出力（自動で重複除去が有効になります）：
```powershell
python main.py --preset tls --merge
```

### 主要なオプション
- `-i, --input`: 入力パス（デフォルト: `01_raw`）
- `-o, --output`: 出力パス（デフォルト: `04_product`）
- `--preset`: `tls` または `uav` を選択。
- `--merge`: 複数ファイルを結合して出力。
- `--dry-run`: 実行せずに生成されるパイプラインを確認。

#### プリセット規定値
| プリセット | 振れ角(ScanAngle) | 入射角確保 | 距離(Range) | ボクセル |
| :--- | :--- | :--- | :--- | :--- |
| **tls** | 0.0 - 86.0° | 4°以上 | 0.5 - 25.0m | 0.01m |
| **uav** | 0.0 - 30.0° | - | 1.0 - 100.0m | 0.05m |

※ 個別の引数（例: `--range-max 50`）を指定することで、プリセット値の一部を上書き可能です。

#### フィルタ詳細

**既存フィルター**
- **入射角**: `--incidence-angle-min`, `--incidence-angle-max`
- **距離**: `--range-min`, `--range-max`, `--origin X Y Z` (器械点手動指定)
- **色**: `--color-clean` (有効化), `--ortho-path <path>`, `--color-threshold <float>`
- **間引き (ボクセル)**: `--voxel-size`
- **重複除去**: `--deduplicate`

**追加フィルター (PR #6〜#12)**

| フィルター | オプション | デフォルト値 | 説明 |
| :--- | :--- | :--- | :--- |
| 統計的外れ値除去 | `--outlier-statistical` | - | 近傍 k 点の平均距離が閾値超の点を削除 |
|  | `--outlier-mean-k K` | 8 | 近傍点数 |
|  | `--outlier-multiplier M` | 2.0 | 標準偏差の倍率閾値 |
| 半径外れ値除去 | `--outlier-radius` | - | 半径 r 内に k 点未満の孤立点を削除 |
|  | `--outlier-radius-value R` | 1.0 | 探索半径 |
|  | `--outlier-min-k K` | 2 | 最低近傍点数 |
| 分類コードフィルター | `--keep-classes C [C ...]` | - | 指定した分類コードの点のみ保持 |
| リターン番号フィルター | `--keep-returns N [N ...]` | - | 指定したリターン番号のみ保持（例: `1` = 1st return のみ）|
| 空間クリップ | `--clip-xmin`, `--clip-xmax` | - | X/Y/Z 境界ボックスで空間切り抜き |
|  | `--clip-ymin`, `--clip-ymax` | - | |
|  | `--clip-zmin`, `--clip-zmax` | - | |
| 高さ(Z)フィルター | `--z-min`, `--z-max` | - | Z 値（高さ）の範囲で点群を絞り込み |
| ポアソンディスク間引き | `--sample-radius R` | - | 半径 R 内に1点を保持するランダム間引き（座標変化なし） |

> **注**: 統計的外れ値・半径外れ値フィルターは内部で2段階処理（`filters.outlier` → `filters.expression: Classification != 7`）を実行します。PDAL の `filters.outlier` は外れ値を `Classification=7` にマークするだけで削除しないため、後続の expression フィルターが必要です。

#### 使用例

```bash
# 統計的外れ値を厳格に除去（k=16、1.5σ）
python cli.py -i input.las -o output.las --outlier-statistical --outlier-mean-k 16 --outlier-multiplier 1.5

# 地面点（Class 2）のみ抽出
python cli.py -i input.las -o output.las --keep-classes 2

# 1st リターンのみ・Z=440〜480m の範囲に絞り込み
python cli.py -i input.las -o output.las --keep-returns 1 --z-min 440 --z-max 480

# ポアソンディスクで 50cm 間隔に均一間引き
python cli.py -i input.las -o output.las --sample-radius 0.5
```

## 評価レポート

フィクスチャ: `tests/fixtures/autzen-small.laz`（7,388 pts、Oregon State Plane、単位: フィート）

### 追加フィルター実測値

| フィルター | 設定 | 入力 | 出力 | 除去率 |
| :--- | :--- | ---: | ---: | ---: |
| 統計的外れ値 | k=8, 2σ (デフォルト) | 7,388 | 7,262 | -1.7% |
| 統計的外れ値 | k=16, 1.5σ (厳格) | 7,388 | 7,221 | -2.3% |
| 半径外れ値 | r=1.0ft, min_k=2 (デフォルト) | 7,388 | 7,388 | 0.0% |
| 半径外れ値 | r=0.5ft, min_k=5 (過剰) | 7,388 | 0 | ⚠️ 全点削除 |
| 分類コード | Ground(2) のみ | 7,388 | 385 | -94.8% |
| 分類コード | Ground(2) + Building(6) | 7,388 | 7,334 | -0.7% |
| リターン番号 | 1st のみ | 7,388 | 7,361 | -0.4% |
| リターン番号 | 1st + 2nd | 7,388 | 7,388 | 0.0% |
| 空間クリップ | SW 象限 (XY) | 7,388 | 1,855 | -74.9% |
| 空間クリップ | Z ≤ 460ft | 7,388 | 885 | -88.0% |
| Z フィルター | Z ≥ 444.8ft | 7,388 | 7,365 | -0.3% |
| Z フィルター | 下半分 (424〜461ft) | 7,388 | 885 | -88.0% |
| ポアソン間引き | r=0.5ft (~15cm) | 7,388 | 4,474 | -39.4% |
| ポアソン間引き | r=2.0ft (~60cm) | 7,388 | 430 | -94.2% |

> ⚠️ 半径外れ値 `r=0.5ft / min_k=5` はデータ点密度に対して過剰な設定のため全点削除。PDAL も警告を出力します（仕様通りの動作）。実運用時は `r=1.0ft / min_k=2` 相当から始め、データに合わせて調整してください。

評価スクリプトを使用してローカルデータで同様の計測を実行できます:
```bash
python tools/evaluate_new_filters.py
```

## 地表分類（Issue #3）

PDAL 標準の地表分類フィルターを CLI から使えます。フィルター（点の削除）とは別機能で、既定では点を消さずに `Classification` を地表 `2` / 非地表 `1` に書き換えます（PDAL の既定で対象は最終・単一リターン。それ以外の点は `0` のまま）。

| 方式 | PDAL | 特徴 |
| :--- | :--- | :--- |
| `smrf` | `filters.smrf` | Simple Morphological Filter（Pingel et al., 2013）。航空 LiDAR 向けの標準。窓サイズより大きい建物は地表扱いになりやすい |
| `pmf` | `filters.pmf` | Progressive Morphological Filter（Zhang et al., 2003）。窓を段階的に広げる。大きい窓ほど建物を除けるが遅い |
| `csf` | `filters.csf` | Cloth Simulation Filter（Zhang et al., 2016）。点群を反転して布をかぶせる方式。狭い範囲・建物が大半を占めるデータでも安定 |

| オプション | 説明 |
| :--- | :--- |
| `--ground-classify {smrf,pmf,csf}` | 地表分類を有効化 |
| `--ground-only` | 分類後に地表（Class 2）のみ残す。PDAL は最終・単一リターンしか分類しないため、それ以外のリターンもあわせて除かれる |
| `--ground-cell C` | グリッドサイズ（smrf `cell` / pmf `cell_size` / csf `resolution`） |
| `--ground-window W` | 最大窓サイズ（smrf `window` / pmf `max_window_size`、csf では使用不可）。最大の建物より大きくする |
| `--ground-threshold T` | 地表とみなす高さの閾値（smrf `threshold` / pmf `max_distance` / csf `threshold`） |
| `--ground-keep-classes` | 分類前に既存の `Classification` を 0 にリセットしない |

長さはすべて入力データの座標単位（通常はメートル、autzen-small はフィート）です。未指定の値は PDAL の既定値を使います。地表分類はノイズ・色クリーニングの後、ボクセル間引きの前に実行されます。

```bash
# CSF で地表のみ抽出（1m グリッド、閾値 0.5m）
python main.py -i input.las -o ground.las --ground-classify csf --ground-cell 1.0 --ground-threshold 0.5 --ground-only

# SMRF で分類のみ（点は残し、Classification を書き換え）
python main.py -i input.las -o classified.las --ground-classify smrf --ground-window 30
```

### 評価結果（autzen-small）

元データのラベル（Class 2 = 385 点）を正解として、地表判定の適合率・再現率を計測しました。このフィクスチャは約 36m 四方で、点の約 95% が 1 棟の屋根（Class 6）です。

| 方式 | 設定 | 地表判定点数 | 適合率 | 再現率 | F1 |
| :--- | :--- | ---: | ---: | ---: | ---: |
| smrf | PDAL 既定 | 7,062 | 0.052 | 0.948 | 0.098 |
| smrf | window=200ft | 7,036 | 0.052 | 0.953 | 0.099 |
| pmf | PDAL 既定 | 5,798 | 0.057 | 0.855 | 0.106 |
| pmf | window=200ft | 1,383 | 0.238 | 0.855 | 0.372 |
| csf | PDAL 既定 | 2,374 | 0.136 | 0.839 | 0.234 |
| csf | resolution=3ft, threshold=1.5ft | 419 | 0.919 | 1.000 | **0.958** |

- 範囲の大半を屋根が覆うため、smrf / pmf（形態学フィルター）は屋根の下の地表面を推定できず、屋根を地表と判定します。窓を範囲より大きくしても改善は限定的です。広い範囲で地表が十分に写っているデータでは結果が変わるため、この表は「狭い範囲・建物主体」の条件での比較です。
- csf は解像度をデータ単位に合わせる（既定 1.0 はメートル想定）だけで F1 0.96 になりました。
- まず `csf` を試し、地形の起伏が大きい航空データでは `smrf` を比較する運用を推奨します。

再現手順:
```bash
make harness                                   # ground_* シナリオを含む回帰ハーネス
uv run python tools/evaluate_ground_classify.py   # 上表の適合率・再現率
```

## 開発とテスト
```powershell
pytest tests/test_filter.py
```

---
License: [MIT](LICENSE)
Copyright (c) 2026 geo-quantz
