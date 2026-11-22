# RELEASE NOTES

---

## Version 1.4.0 (予定)

- [x] MCP サーバー（`gqfilter mcp`）: AI から 1 ファイルずつフィルタを呼べる。ツールは `gqfilter_run`（実行）、`gqfilter_dry_run`（パイプラインの確認）、`gqfilter_info`（オプションの一覧）。引数は CLI のオプションと同じ名前・同じ既定値で、CLI と同じ処理を通る。フォルダの一括処理のツールは無い
- [x] 実行ファイル（PyInstaller）に MCP SDK（`mcp` 2 系）を入れる。macOS の実行ファイルは約 25 MB 大きくなる（うち約 7 MB は PDAL のライブラリの重複で、MCP とは関係ない）
- [x] `gq_filter.__version__` を追加
- [x] Windows のインストーラ（MSI）の版を 1.4.0.0 にする
- [ ] Windows（MSI）・Linux の実行ファイルで `gqfilter mcp` を確かめる

---

## Version 1.3.0 (2026/11/19)

- [x] デスクトップアプリ（`apps/desktop/`、Tauri）: 点群ファイルを選ぶ → フィルタを選ぶ → 出力先を選ぶ → 実行 → 入力と出力の点の数を見る。処理はインストールした gqfilter を呼ぶ
- [x] アプリのインストーラをリリースに追加（Windows: `gq-filter_1.3.0_x64-setup.exe`、macOS: `gq-filter_1.3.0_aarch64.dmg`、Linux: `gq-filter_1.3.0_amd64.deb`）。署名はまだ無い
- [x] macOS で PDAL のプラグインの警告にくっついたエラーも、本当の理由を出す。警告は灰色で出す
- [x] Windows のインストーラ（MSI）の版を 1.3.0.0 にする（前の版から上書きで入れ替えられるように）

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
