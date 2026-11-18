import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Api, LogLine, RunRequest, RunResult } from "./api";
import { App } from "./App";

function fakeApi(overrides: Partial<Api> = {}) {
  let logHandler: ((line: LogLine) => void) | null = null;
  const runFilter = vi.fn(
    async (_: RunRequest): Promise<RunResult> => ({
      success: true,
      cancelled: false,
      exitCode: 0,
      outputPoints: 7262,
      error: null,
    }),
  );
  const api: Api = {
    cliStatus: vi.fn(async () => ({ found: true, path: "/opt/gqfilter/gqfilter", source: "setting" as const, message: null })),
    inspectInput: vi.fn(async () => ({ format: "LAZ 1.2", points: 7388 })),
    prepareOutput: vi.fn(async (_i: string, folder: string, format: string) => ({
      path: `${folder}/autzen-small_filtered.${format}`,
      exists: false,
    })),
    runFilter,
    cancelRun: vi.fn(async () => true),
    onLog: vi.fn(async (handler: (line: LogLine) => void) => {
      logHandler = handler;
      return () => undefined;
    }),
    pickInput: vi.fn(async () => "/data/autzen-small.laz"),
    pickFolder: vi.fn(async () => "/out"),
    pickCli: vi.fn(async () => "/opt/gqfilter"),
    confirmOverwrite: vi.fn(async () => false),
    ...overrides,
  };
  return { api, runFilter, emit: (line: LogLine) => logHandler?.(line) };
}

async function pickInputAndEnable(sectionLabel: string) {
  fireEvent.click(screen.getByRole("button", { name: "入力ファイルを選ぶ" }));
  await screen.findByText(/7,388 点/);
  fireEvent.click(screen.getByRole("checkbox", { name: sectionLabel }));
}

describe("App", () => {
  it("runs gqfilter with the built arguments and shows the counts", async () => {
    const { api, runFilter, emit } = fakeApi();
    render(<App api={api} />);
    await screen.findByText("/opt/gqfilter/gqfilter");

    await pickInputAndEnable("統計的外れ値除去");
    await screen.findByText("/data/autzen-small_filtered.laz");
    fireEvent.click(screen.getByRole("button", { name: "実行" }));

    await screen.findByText("完了しました（0 秒）");
    expect(runFilter).toHaveBeenCalledWith({
      cliSetting: null,
      input: "/data/autzen-small.laz",
      folder: "/data",
      format: "laz",
      filterArgs: ["--outlier-statistical", "--outlier-mean-k", "8", "--outlier-multiplier", "2"],
      overwrite: false,
    });
    expect(screen.getByText("入力 7,388 点 → 出力 7,262 点（-126 点、-1.7%）")).toBeInTheDocument();

    act(() => emit({ stream: "stdout", line: "Points processed: 7262" }));
    expect(screen.getByText(/Points processed: 7262/)).toBeInTheDocument();
  });

  it("shows form errors and does not run", async () => {
    const { api, runFilter } = fakeApi();
    render(<App api={api} />);
    await pickInputAndEnable("高さ (Z)");
    fireEvent.change(screen.getByRole("textbox", { name: "下限" }), { target: { value: "abc" } });
    await screen.findByText("/data/autzen-small_filtered.laz");
    fireEvent.click(screen.getByRole("button", { name: "実行" }));
    expect(await screen.findByText("高さ (Z)の下限: 数で入れてください。")).toBeInTheDocument();
    expect(runFilter).not.toHaveBeenCalled();
  });

  it("asks before overwriting and stops when declined", async () => {
    const { api, runFilter } = fakeApi({
      prepareOutput: vi.fn(async () => ({ path: "/data/autzen-small_filtered.laz", exists: true })),
    });
    render(<App api={api} />);
    await pickInputAndEnable("重複点の除去");
    await screen.findByText(/もうあります/);
    fireEvent.click(screen.getByRole("button", { name: "実行" }));
    await waitFor(() => expect(api.confirmOverwrite).toHaveBeenCalled());
    expect(runFilter).not.toHaveBeenCalled();
  });

  it("shows the CLI error when gqfilter fails", async () => {
    const { api } = fakeApi({
      runFilter: vi.fn(async () => ({
        success: false,
        cancelled: false,
        exitCode: 1,
        outputPoints: null,
        error: "Pipeline execution failed: bad input",
      })),
    });
    render(<App api={api} />);
    await pickInputAndEnable("重複点の除去");
    await screen.findByText("/data/autzen-small_filtered.laz");
    fireEvent.click(screen.getByRole("button", { name: "実行" }));
    expect(await screen.findByText("失敗しました")).toBeInTheDocument();
    expect(screen.getByText("Pipeline execution failed: bad input")).toBeInTheDocument();
  });

  it("says clearly when gqfilter is missing and keeps run disabled", async () => {
    const { api } = fakeApi({
      cliStatus: vi.fn(async () => ({ found: false, path: null, source: null, message: "gqfilter が見つかりません。" })),
    });
    render(<App api={api} />);
    expect(await screen.findByText("gqfilter が見つかりません。")).toBeInTheDocument();
    await pickInputAndEnable("重複点の除去");
    expect(screen.getByRole("button", { name: "実行" })).toBeDisabled();
  });
});
