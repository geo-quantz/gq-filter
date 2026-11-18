// The window's only way out: Tauri commands (src-tauri/src/commands.rs) and
// the file dialogs. Kept behind an interface so the screen can be tested.

import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { ask, open } from "@tauri-apps/plugin-dialog";

export interface CliStatus {
  readonly found: boolean;
  readonly path: string | null;
  readonly source: "setting" | "env" | "path" | "install" | null;
  readonly message: string | null;
}

export interface HeaderInfo {
  readonly format: string;
  readonly points: number;
}

export interface PlannedOutput {
  readonly path: string;
  readonly exists: boolean;
}

export interface RunRequest {
  readonly cliSetting: string | null;
  readonly input: string;
  readonly folder: string;
  readonly format: OutputFormat;
  readonly filterArgs: readonly string[];
  readonly overwrite: boolean;
}

export interface RunResult {
  readonly success: boolean;
  readonly cancelled: boolean;
  readonly exitCode: number | null;
  readonly outputPoints: number | null;
  readonly error: string | null;
}

export interface LogLine {
  readonly stream: "stdout" | "stderr";
  readonly line: string;
}

export type OutputFormat = "las" | "laz";

export interface Api {
  cliStatus(setting: string): Promise<CliStatus>;
  inspectInput(path: string): Promise<HeaderInfo>;
  prepareOutput(input: string, folder: string, format: OutputFormat): Promise<PlannedOutput>;
  runFilter(request: RunRequest): Promise<RunResult>;
  cancelRun(): Promise<boolean>;
  /** Subscribes to gqfilter output lines; returns the unsubscribe function. */
  onLog(handler: (line: LogLine) => void): Promise<() => void>;
  pickInput(): Promise<string | null>;
  pickFolder(): Promise<string | null>;
  pickCli(): Promise<string | null>;
  confirmOverwrite(path: string): Promise<boolean>;
}

function single(picked: string | string[] | null): string | null {
  if (picked === null) return null;
  return Array.isArray(picked) ? (picked[0] ?? null) : picked;
}

export const tauriApi: Api = {
  cliStatus: (setting) => invoke<CliStatus>("cli_status", { setting }),
  inspectInput: (path) => invoke<HeaderInfo>("inspect_input", { path }),
  prepareOutput: (input, folder, format) => invoke<PlannedOutput>("prepare_output", { input, folder, format }),
  runFilter: (request) => invoke<RunResult>("run_filter", { request }),
  cancelRun: () => invoke<boolean>("cancel_run"),
  onLog: (handler) => listen<LogLine>("filter-log", (event) => handler(event.payload)),
  pickInput: async () =>
    single(
      await open({
        title: "入力の点群ファイル",
        multiple: false,
        filters: [{ name: "点群 (LAS / LAZ / E57)", extensions: ["las", "laz", "e57", "LAS", "LAZ", "E57"] }],
      }),
    ),
  pickFolder: async () => single(await open({ title: "出力フォルダ", directory: true, multiple: false })),
  pickCli: async () => single(await open({ title: "gqfilter の実行ファイル", multiple: false })),
  confirmOverwrite: (path) =>
    ask(`出力ファイルがもうあります。上書きしますか？\n${path}`, { title: "上書きの確認", kind: "warning" }),
};
