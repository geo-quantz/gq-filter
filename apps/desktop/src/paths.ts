// Small path helpers for display and defaults. The Rust side builds the real paths.

/** The folder part of a path ("/a/b/c.las" -> "/a/b", "C:\\d\\c.las" -> "C:\\d"). */
export function parentDir(path: string): string {
  const cut = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  if (cut < 0) return "";
  if (cut === 0) return path.slice(0, 1);
  return path.slice(0, cut);
}

/** The default output format: LAS stays LAS, everything else (LAZ, E57) becomes LAZ. */
export function defaultFormat(inputPath: string): "las" | "laz" {
  return inputPath.toLowerCase().endsWith(".las") ? "las" : "laz";
}

/** "7,388 → 7,262 点（-126 点、-1.7%）" */
export function describeCounts(input: number | null, output: number | null): string {
  const fmt = (n: number) => n.toLocaleString("ja-JP");
  if (output === null) return "";
  if (input === null) return `出力 ${fmt(output)} 点`;
  const diff = output - input;
  const pct = input === 0 ? 0 : (diff / input) * 100;
  const sign = diff > 0 ? "+" : "";
  return `入力 ${fmt(input)} 点 → 出力 ${fmt(output)} 点（${sign}${fmt(diff)} 点、${sign}${pct.toFixed(1)}%）`;
}
