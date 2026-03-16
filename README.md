# PDAL Filter CLI

点群ファイルに対して PDAL フィルターパイプラインを実行するコマンドラインツールです。
入射角・強度・距離・重複点によるフィルタリングをサポートします。

## 使い方

```
pdal_filter --input <入力ファイル> --output <出力ファイル> [オプション]
```

### 主なオプション

| オプション | 説明 |
|---|---|
| `--input`, `-i` | 入力点群ファイルのパス（必須） |
| `--output`, `-o` | 出力点群ファイルのパス（必須） |
| `--dry-run` | パイプラインの内容を表示するだけで実行しない |
| `--incidence-angle-max` | 最大入射角（ScanAngleRank の絶対値） |
| `--intensity-min` | 強度の最小値 |
| `--intensity-max` | 強度の最大値 |
| `--range-min` | 原点からの最小距離 |
| `--range-max` | 原点からの最大距離 |
| `--deduplicate` | 重複点（XYZ が完全一致）を除去する |

### 実行例

```bash
# 強度フィルターと重複除去
pdal_filter --input input.las --output output.las --intensity-min 100 --deduplicate

# 入射角と距離のフィルター
pdal_filter --input input.las --output output.las --incidence-angle-max 45 --range-max 200

# パイプラインの内容を確認（実行しない）
pdal_filter --input input.las --output output.las --deduplicate --dry-run
```

## OS 別のビルド・セットアップガイド

スタンドアロン実行ファイルのビルド方法は、OS ごとのガイドを参照してください。

| OS | ガイド |
|---|---|
| macOS | [README_macos.md](README_macos.md) |
| Linux | [README_linux.md](README_linux.md) |
| Windows | [README_windows.md](README_windows.md) |

### 各 OS の概要

**macOS**
- Homebrew または Conda で PDAL をインストール
- `scripts/build_macos.sh` を実行してビルド
- Apple Silicon (arm64) / Intel (x86_64) の両アーキテクチャに対応
- Gatekeeper による実行ブロックへの対処方法あり

**Linux**
- Conda（推奨）またはシステムパッケージマネージャーで PDAL をインストール
- `scripts/build_linux.sh` を実行してビルド
- 最大互換性のために、サポート対象の最も古いディストリビューションでビルドすることを推奨

**Windows**
- Python 3.9+ と PDAL（Conda 推奨）をインストール
- PowerShell: `.\scripts\build_windows.ps1`、またはバッチ: `scripts\build_windows.bat` でビルド
- WiX Toolset v3 があれば MSI インストーラーも生成可能

## プロジェクト構成

```
.
├── cli.py              # CLI エントリーポイント
├── main.py             # メインモジュール
├── lib/
│   └── filter.py       # フィルター定義とパイプラインビルダー
├── tests/
│   └── test_filter.py  # ユニットテスト
├── scripts/            # OS 別ビルドスクリプト
└── packaging/          # PyInstaller スペックファイル
```
