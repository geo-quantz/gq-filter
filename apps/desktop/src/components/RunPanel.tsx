// Run status: elapsed time while gqfilter runs, then the result and the log.

import { useEffect, useRef, useState } from "react";
import type { LogLine, RunResult } from "../api";
import { describeCounts } from "../paths";

export type RunPhase =
  | { readonly kind: "idle" }
  | { readonly kind: "running"; readonly startedAt: number }
  | { readonly kind: "done"; readonly result: RunResult; readonly seconds: number; readonly output: string }
  | { readonly kind: "failed"; readonly message: string };

interface Props {
  readonly phase: RunPhase;
  readonly log: readonly LogLine[];
  readonly inputPoints: number | null;
}

function useElapsed(startedAt: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (startedAt === null) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);
  return startedAt === null ? 0 : Math.max(0, Math.floor((now - startedAt) / 1000));
}

function Result({ phase, inputPoints }: { readonly phase: RunPhase; readonly inputPoints: number | null }) {
  const elapsed = useElapsed(phase.kind === "running" ? phase.startedAt : null);
  switch (phase.kind) {
    case "idle":
      return null;
    case "running":
      return (
        <div className="status-box running" role="status">
          <span className="progress" aria-hidden="true" />
          実行中… {elapsed} 秒
        </div>
      );
    case "failed":
      return (
        <div className="status-box error" role="alert">
          <strong>実行できませんでした</strong>
          <pre>{phase.message}</pre>
        </div>
      );
    case "done": {
      const { result } = phase;
      if (!result.success) {
        return (
          <div className={result.cancelled ? "status-box warn" : "status-box error"} role="alert">
            <strong>{result.cancelled ? "中止しました" : "失敗しました"}</strong>
            <pre>{result.error ?? ""}</pre>
          </div>
        );
      }
      return (
        <div className="status-box ok" role="status">
          <strong>完了しました（{phase.seconds} 秒）</strong>
          <p className="counts">{describeCounts(inputPoints, result.outputPoints)}</p>
          <p className="path">{phase.output}</p>
        </div>
      );
    }
  }
}

export function RunPanel({ phase, log, inputPoints }: Props) {
  const logRef = useRef<HTMLPreElement>(null);
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [log.length]);
  return (
    <section className="run-panel" aria-label="実行の結果">
      <Result phase={phase} inputPoints={inputPoints} />
      {log.length > 0 && (
        <details open className="log-box">
          <summary>gqfilter の出力（{log.length} 行）</summary>
          <pre ref={logRef} className="log">
            {log.map((l, i) => (
              <span key={i} className={l.stream === "stderr" ? "stderr" : undefined}>
                {l.line}
                {"\n"}
              </span>
            ))}
          </pre>
        </details>
      )}
    </section>
  );
}
