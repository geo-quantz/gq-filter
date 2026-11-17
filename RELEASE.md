# RELEASE NOTES

---

## Version 1.3.0 (予定)

- [x] 大容量ファイルモード: `--stream {off,on,auto}` / `--chunk-size` / `--stream-threshold-mb`（PDAL のストリーム実行）。ストリーム非対応のフィルターは名前を示してエラー（on）または通常実行に戻す（auto）
- [x] タイル分割モード: `--tile` / `--tile-buffer` / `--work-dir`。ストリーム非対応のフィルターを大容量ファイルに使う
- [x] ライブラリ API: `gq_filter.stream`（`StreamParams`, `run_pipeline`, `blocking_stages`）、`gq_filter.tiling`（`TileParams`, `run_tiled`）
- [x] 並列処理: `--jobs N`（既定は CPU コア数）。複数ファイルはファイル単位、`--tile` はタイル単位でプロセス並列。`cli.py` は `-i` に複数ファイル・ディレクトリを受け付ける。`main.py` の `--max-workers` は `--jobs` の旧名（既定を CPU コア数 - 1 から CPU コア数に変更）
- [ ] リリース前に配布ビルド（PyInstaller）で複数ファイル `--jobs 2` と `--tile --jobs 2` を動かして確認する（spawn のワーカー起動に `freeze_support()` が要る）
- [x] ライブラリ API: `gq_filter.parallel`（`run_files`, `batch_outputs`, `collect_inputs`, `process_map`）、`run_pipeline(..., jobs=N)`
- [x] LAS/LAZ 出力の offset を入力の座標の格子に合わせる（`run_pipeline` 経由。`offset: auto` による最大 0.5 mm のずれをなくす）
- [ ] 簡易 GUI の作成（入出力ファイル指定のみの製品化向け GUI）

---

## Version 1.2.0 (予定)

- [ ] color-clean 機能の改善（TLS 点群の色情報に含まれる車・通行人の映り込み除去）

---

## Version 1.1.0 (予定)

- [ ] 自動分類の実装（PDAL 標準の分類機能を活用する方向で検討）

---

## Version 1.0.3 (2026/03/02)

- [x] CLI ツールの実装（`cli.py`）
- [x] `--input` / `--output` による入出力ファイル指定
- [x] `--incidence-angle-max` による入射角フィルター
- [x] `--intensity-min` / `--intensity-max` による強度フィルター
- [x] `--range-min` / `--range-max` による測定距離フィルター
- [x] `--deduplicate` による重複点除去フラグ
- [x] `--dry-run` によるパイプライン内容の事前確認機能

---

## Version 1.0.2 (2026/02/21)

- [x] クロスプラットフォーム対応ビルドスクリプトの追加（Linux / macOS / Windows）
- [x] PyInstaller スペックファイルによるスタンドアロン実行ファイルのパッケージング
- [x] Windows MSI インストーラーの生成対応（WiX Toolset v3）
- [x] `lib/filter.py` のコンポーザブル・データドリブン構成へのリファクタリング
- [x] `FilterType` クラスによるフィルター型定義の一元化
- [x] パイプラインビルダーのユニットテスト追加（`tests/test_filter.py`）
- [x] OS 別セットアップガイド（`README_linux.md` / `README_macos.md` / `README_windows.md`）

---

## Version 1.0.1 (2026/01/10)

- [x] プロジェクト初期構成（依存関係・設定ファイル）
- [x] メインエントリーポイントの追加（`main.py`）
- [x] 点群フィルタリングユーティリティの実装（`lib/filter.py`）
  - 入射角フィルター（`ScanAngleRank` ベース）
  - 強度フィルター
  - 測定距離フィルター（原点からのユークリッド距離）
  - 重複点除去フィルター（`filters.unique`）
- [x] PDAL パイプラインの組み立て・実行機能
- [x] 出力ファイル拡張子による自動ライタータイプ選択（`.las` / `.copc.laz` / `.txt` / `.csv`）
- [x] `filters.unique` 未対応環境でのフォールバック処理
