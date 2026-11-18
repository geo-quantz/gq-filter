// One screen: input file → filters → output folder → run.

import { useCallback, useEffect, useState } from "react";
import type { Api, CliStatus, HeaderInfo, LogLine, OutputFormat, PlannedOutput } from "./api";
import { buildFilterArgs, type FormError } from "./args/build";
import { applyPatch, DEFAULT_FORM, type FilterForm, type FormPatch } from "./args/form";
import { FilterSections } from "./components/FilterSections";
import { RunPanel, type RunPhase } from "./components/RunPanel";
import { defaultFormat, parentDir } from "./paths";
import { loadCliPath, saveCliPath } from "./settings";

const MAX_LOG_LINES = 5000;

interface InputState {
  readonly path: string;
  readonly info: HeaderInfo | null;
  readonly error: string | null;
}

function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function CliSetting(props: {
  readonly value: string;
  readonly status: CliStatus | null;
  readonly disabled: boolean;
  readonly onChange: (value: string) => void;
  readonly onPick: () => void;
  readonly onCheck: () => void;
}) {
  const { status } = props;
  return (
    <details className="cli-setting" open={status !== null && !status.found}>
      <summary>
        gqfilter の場所:{" "}
        {status === null ? "確認中…" : status.found ? <span className="ok">{status.path}</span> : <span className="ng">見つかりません</span>}
      </summary>
      <p className="help">
        リリースの gqfilter（gqfilter_windows.zip / msi / macos.zip / linux.tar.gz）を使います。空のときは環境変数 GQFILTER_PATH、PATH、よくある場所の順に探します。
      </p>
      <div className="row">
        <input
          type="text"
          aria-label="gqfilter の場所"
          value={props.value}
          placeholder="gqfilter の実行ファイルか、それが入ったフォルダ"
          disabled={props.disabled}
          onChange={(e) => props.onChange(e.target.value)}
          onBlur={props.onCheck}
        />
        <button type="button" disabled={props.disabled} onClick={props.onPick}>
          選ぶ
        </button>
        <button type="button" disabled={props.disabled} onClick={props.onCheck}>
          確かめる
        </button>
      </div>
      {status !== null && !status.found && <p className="ng">{status.message}</p>}
    </details>
  );
}

export function App({ api }: { readonly api: Api }) {
  const [cliPath, setCliPath] = useState(loadCliPath);
  const [cliStatus, setCliStatus] = useState<CliStatus | null>(null);
  const [input, setInput] = useState<InputState | null>(null);
  const [folder, setFolder] = useState("");
  const [format, setFormat] = useState<OutputFormat>("laz");
  const [planned, setPlanned] = useState<PlannedOutput | null>(null);
  const [plannedError, setPlannedError] = useState<string | null>(null);
  const [form, setForm] = useState<FilterForm>(DEFAULT_FORM);
  const [errors, setErrors] = useState<readonly FormError[]>([]);
  const [triedRun, setTriedRun] = useState(false);
  const [phase, setPhase] = useState<RunPhase>({ kind: "idle" });
  const [log, setLog] = useState<readonly LogLine[]>([]);
  const running = phase.kind === "running";

  const checkCli = useCallback(
    (setting: string) => {
      saveCliPath(setting);
      api.cliStatus(setting).then(setCliStatus, (e) =>
        setCliStatus({ found: false, path: null, source: null, message: messageOf(e) }),
      );
    },
    [api],
  );

  useEffect(() => checkCli(loadCliPath()), [checkCli]);

  useEffect(() => {
    let unsubscribe: (() => void) | null = null;
    let active = true;
    void api.onLog((line) => setLog((prev) => [...prev.slice(-(MAX_LOG_LINES - 1)), line])).then((off) => {
      if (active) unsubscribe = off;
      else off();
    });
    return () => {
      active = false;
      unsubscribe?.();
    };
  }, [api]);

  useEffect(() => {
    if (input === null || folder === "") {
      setPlanned(null);
      setPlannedError(null);
      return;
    }
    let current = true;
    api.prepareOutput(input.path, folder, format).then(
      (p) => {
        if (!current) return;
        setPlanned(p);
        setPlannedError(null);
      },
      (e) => {
        if (!current) return;
        setPlanned(null);
        setPlannedError(messageOf(e));
      },
    );
    return () => {
      current = false;
    };
  }, [api, input, folder, format, phase]);

  const changeForm = (patch: FormPatch) => {
    const next = applyPatch(form, patch);
    setForm(next);
    if (triedRun) {
      const built = buildFilterArgs(next);
      setErrors(built.ok ? [] : built.errors);
    }
  };

  const resetForm = () => {
    setForm(DEFAULT_FORM);
    setErrors([]);
    setTriedRun(false);
  };

  const pickInput = async () => {
    const path = await api.pickInput();
    if (path === null) return;
    setInput({ path, info: null, error: null });
    setFormat(defaultFormat(path));
    if (folder === "") setFolder(parentDir(path));
    api.inspectInput(path).then(
      (info) => setInput({ path, info, error: null }),
      (e) => setInput({ path, info: null, error: messageOf(e) }),
    );
  };

  const pickFolder = async () => {
    const path = await api.pickFolder();
    if (path !== null) setFolder(path);
  };

  const pickCli = async () => {
    const path = await api.pickCli();
    if (path === null) return;
    setCliPath(path);
    checkCli(path);
  };

  const run = async () => {
    setTriedRun(true);
    const built = buildFilterArgs(form);
    setErrors(built.ok ? [] : built.errors);
    if (!built.ok || input === null || folder === "") return;
    try {
      const out = await api.prepareOutput(input.path, folder, format);
      if (out.exists && !(await api.confirmOverwrite(out.path))) return;
      setLog([]);
      const startedAt = Date.now();
      setPhase({ kind: "running", startedAt });
      const result = await api.runFilter({
        cliSetting: cliPath.trim() === "" ? null : cliPath,
        input: input.path,
        folder,
        format,
        filterArgs: built.args,
        overwrite: out.exists,
      });
      const seconds = Math.round((Date.now() - startedAt) / 1000);
      setPhase({ kind: "done", result, seconds, output: out.path });
    } catch (e) {
      setPhase({ kind: "failed", message: messageOf(e) });
    }
  };

  const formError = errors.find((e) => e.section === null);
  const canRun = !running && input !== null && folder !== "" && cliStatus?.found === true && plannedError === null;

  return (
    <div className="app">
      <header className="topbar">
        <span className="title">gq-filter</span>
        <span className="sub">点群フィルター（gqfilter の画面）</span>
      </header>
      <main>
        <CliSetting
          value={cliPath}
          status={cliStatus}
          disabled={running}
          onChange={setCliPath}
          onPick={() => void pickCli()}
          onCheck={() => checkCli(cliPath)}
        />
        <section className="step">
          <h2>1. 入力ファイル</h2>
          <div className="row">
            <button type="button" aria-label="入力ファイルを選ぶ" disabled={running} onClick={() => void pickInput()}>
              選ぶ
            </button>
            <span className="path">{input?.path ?? "LAS / LAZ / E57 のファイルを選んでください"}</span>
          </div>
          {input?.info && (
            <p className="sub">
              {input.info.format}・{input.info.points.toLocaleString("ja-JP")} 点
            </p>
          )}
          {input?.error && <p className="ng">{input.error}</p>}
        </section>

        <section className="step">
          <div className="step-head">
            <h2>2. フィルター</h2>
            <button type="button" className="link" disabled={running} onClick={resetForm}>
              すべて外して既定値に戻す
            </button>
          </div>
          <p className="help">チェックしたものを gqfilter に渡します。順番は gqfilter が決めます（オプションは CLI と同じです）。</p>
          <FilterSections form={form} errors={errors} disabled={running} onChange={changeForm} />
          {formError && <p className="ng">{formError.message}</p>}
        </section>

        <section className="step">
          <h2>3. 出力</h2>
          <div className="row">
            <button type="button" disabled={running} onClick={() => void pickFolder()}>
              フォルダを選ぶ
            </button>
            <span className="path">{folder || "出力フォルダを選んでください"}</span>
          </div>
          <div className="row">
            <label className="field inline">
              <span>形式</span>
              <select value={format} disabled={running} onChange={(e) => setFormat(e.target.value as OutputFormat)}>
                <option value="laz">LAZ（圧縮）</option>
                <option value="las">LAS</option>
              </select>
            </label>
            {planned && (
              <span className="sub">
                {planned.path}
                {planned.exists && <span className="warn-text">（もうあります。実行すると上書きの確認をします）</span>}
              </span>
            )}
          </div>
          {plannedError && <p className="ng">{plannedError}</p>}
        </section>

        <section className="step run-row">
          <button type="button" className="primary" disabled={!canRun} onClick={() => void run()}>
            実行
          </button>
          {running && (
            <button type="button" className="danger" onClick={() => void api.cancelRun()}>
              中止
            </button>
          )}
          {!running && cliStatus?.found === false && <span className="ng">先に gqfilter の場所を設定してください。</span>}
        </section>
        <RunPanel phase={phase} log={log} inputPoints={input?.info?.points ?? null} />
      </main>
    </div>
  );
}
