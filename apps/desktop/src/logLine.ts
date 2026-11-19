// How a line of gqfilter output is shown in the log. PDAL prints harmless
// messages when an optional plugin (for example the HDF5 reader) cannot be
// loaded; they stay in the log but are not shown in red like real errors.
// Same rules as `is_plugin_noise` in src-tauri/src/run_args.rs.

import type { LogLine } from "./api";

const PIPELINE_MARKER = "Pipeline execution failed:";
const PLUGIN_NOISE_WORDS = ["dlopen", "unable to load", "failed to load", "library not loaded"] as const;

// `Error:` at the end of a Python exception name (`RuntimeError:`) does not count.
function hasErrorMarker(line: string): boolean {
  if (line.includes(PIPELINE_MARKER)) return true;
  return [...line.matchAll(/Error:/g)].some((m) => {
    const before = line.slice(0, m.index);
    const word = /[A-Za-z0-9]*$/.exec(before)?.[0] ?? "";
    return !/^[A-Z]/.test(word);
  });
}

export function isPluginNoise(line: string): boolean {
  const lower = line.toLowerCase();
  return (
    lower.includes("plugin") &&
    PLUGIN_NOISE_WORDS.some((w) => lower.includes(w)) &&
    !hasErrorMarker(line)
  );
}

export function logLineClass(l: LogLine): string | undefined {
  if (l.stream !== "stderr") return undefined;
  return isPluginNoise(l.line) ? "stderr noise" : "stderr";
}
