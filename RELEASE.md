# RELEASE NOTES

---

## Version 1.3.0 (予定)

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
