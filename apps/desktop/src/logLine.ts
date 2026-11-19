// How a line of gqfilter output is shown in the log. PDAL prints harmless
// messages when an optional plugin (for example the HDF5 reader) cannot be
// loaded; they stay in the log but are not shown in red like real errors.
// Same rules as `is_plugin_noise` in src-tauri/src/run_args.rs.

import type { LogLine } from "./api";

const PIPELINE_MARKER = "Pipeline execution failed:";
const PLUGIN_NOISE_WORDS = ["dlopen", "unable to load", "failed to load", "library not loaded"] as const;

function looksLikePluginMessage(text: string): boolean {
  const lower = text.toLowerCase();
  return lower.includes("plugin") && PLUGIN_NOISE_WORDS.some((w) => lower.includes(w));
}

// `Error:` counts at the start of the line or after a plugin-load message it
// was glued onto, so `RuntimeError:` or `Fatal Error:` from other code does not.
function hasErrorMarker(raw: string): boolean {
  const line = raw.trim();
  if (line.includes(PIPELINE_MARKER)) return true;
  return [...line.matchAll(/Error:/g)].some(
    (m) => m.index === 0 || looksLikePluginMessage(line.slice(0, m.index)),
  );
}

export function isPluginNoise(line: string): boolean {
  return looksLikePluginMessage(line) && !hasErrorMarker(line);
}

export function logLineClass(l: LogLine): string | undefined {
  if (l.stream !== "stderr") return undefined;
  return isPluginNoise(l.line) ? "stderr noise" : "stderr";
}
