// Turns the filter form into gqfilter (cli.py) arguments.
// Pure: no I/O, the form is not changed. Input and output paths are added by
// the Rust side, which also checks every flag against the same list.
// tests/test_desktop_args.py parses the argv of src/args/cases.json with the
// real CLI parser, so the flag names here must match cli.py exactly.

import type { FilterForm, SectionKey } from "./form";

export const SECTION_LABELS: Readonly<Record<SectionKey, string>> = {
  incidence: "入射角",
  intensity: "強度",
  range: "距離",
  deduplicate: "重複点の除去",
  statistical: "統計的外れ値除去",
  radiusOutlier: "半径外れ値除去",
  classes: "分類コード",
  returns: "リターン番号",
  clip: "空間クリップ",
  heightZ: "高さ (Z)",
  sample: "ポアソンディスク間引き",
  ground: "地表分類",
};

/** Every flag this builder can emit (the Rust side allows only these). */
export const CLI_FLAGS = [
  "--incidence-angle-max",
  "--intensity-min",
  "--intensity-max",
  "--range-min",
  "--range-max",
  "--deduplicate",
  "--outlier-statistical",
  "--outlier-mean-k",
  "--outlier-multiplier",
  "--outlier-radius",
  "--outlier-radius-value",
  "--outlier-min-k",
  "--keep-classes",
  "--keep-returns",
  "--clip-xmin",
  "--clip-xmax",
  "--clip-ymin",
  "--clip-ymax",
  "--clip-zmin",
  "--clip-zmax",
  "--z-min",
  "--z-max",
  "--sample-radius",
  "--ground-classify",
  "--ground-cell",
  "--ground-window",
  "--ground-threshold",
  "--ground-only",
  "--ground-keep-classes",
] as const;

type Flag = (typeof CLI_FLAGS)[number];

export interface FormError {
  /** The form section the error belongs to; null for the form as a whole. */
  readonly section: SectionKey | null;
  readonly message: string;
}

export type BuildResult =
  | { readonly ok: true; readonly args: readonly string[] }
  | { readonly ok: false; readonly errors: readonly FormError[] };

// --- small parsers. Each returns a value or an error message. ---

type Parsed<T> = { readonly value: T } | { readonly error: string };

function parseNumber(text: string, label: string, required: boolean): Parsed<number | null> {
  const trimmed = text.trim();
  if (trimmed === "") return required ? { error: `${label}: 値を入れてください。` } : { value: null };
  const value = Number(trimmed);
  if (!Number.isFinite(value)) return { error: `${label}: 数で入れてください。` };
  return { value };
}

function parsePositive(text: string, label: string, required: boolean): Parsed<number | null> {
  const parsed = parseNumber(text, label, required);
  if ("error" in parsed || parsed.value === null) return parsed;
  return parsed.value > 0 ? parsed : { error: `${label}: 0 より大きい数にしてください。` };
}

function parseCount(text: string, label: string): Parsed<number> {
  const parsed = parseNumber(text, label, true);
  if ("error" in parsed) return parsed;
  const value = parsed.value ?? 0;
  return Number.isInteger(value) && value >= 1 ? { value } : { error: `${label}: 1 以上の整数にしてください。` };
}

function parseIntList(text: string, label: string, min: number, max: number): Parsed<number[]> {
  const parts = text.split(/[\s,、]+/).filter((p) => p !== "");
  if (parts.length === 0) return { error: `${label}: 1 つ以上入れてください。` };
  const values: number[] = [];
  for (const part of parts) {
    const value = Number(part);
    if (!Number.isInteger(value) || value < min || value > max) {
      return { error: `${label}: ${min}〜${max} の整数をカンマか空白で区切って入れてください（${part}）。` };
    }
    values.push(value);
  }
  return { value: values };
}

// --- one builder per section. Each returns args or errors. ---

type SectionResult = { readonly args: readonly string[]; readonly errors: readonly string[] };

class Collector {
  private readonly args: string[] = [];
  private readonly errors: string[] = [];

  /** Adds `flag value` when the parsed value is present; records the error otherwise. */
  take<T extends number | null>(flag: Flag, parsed: Parsed<T>): T | null {
    if ("error" in parsed) {
      this.errors.push(parsed.error);
      return null;
    }
    if (parsed.value !== null) this.args.push(flag, String(parsed.value));
    return parsed.value;
  }

  list(flag: Flag, parsed: Parsed<number[]>): void {
    if ("error" in parsed) this.errors.push(parsed.error);
    else this.args.push(flag, ...parsed.value.map(String));
  }

  flag(flag: Flag): void {
    this.args.push(flag);
  }

  /** A flag with a fixed choice (not a number), e.g. the ground method. */
  choice(flag: Flag, value: string): void {
    this.args.push(flag, value);
  }

  error(message: string): void {
    this.errors.push(message);
  }

  result(): SectionResult {
    return { args: [...this.args], errors: [...this.errors] };
  }
}

/** Optional lower / upper bound pair; at least one is required, lower <= upper. */
function minMax(c: Collector, label: string, min: [Flag, string, string], max: [Flag, string, string]): void {
  const lo = c.take(min[0], parseNumber(min[1], `${label}の${min[2]}`, false));
  const hi = c.take(max[0], parseNumber(max[1], `${label}の${max[2]}`, false));
  if (min[1].trim() === "" && max[1].trim() === "") c.error(`${label}: 下限か上限のどちらかを入れてください。`);
  if (lo !== null && hi !== null && lo > hi) c.error(`${label}: 下限が上限より大きくなっています。`);
}

type Builders = { readonly [K in SectionKey]: (section: FilterForm[K], label: string) => SectionResult };

const BUILDERS: Builders = {
  incidence: (s, label) => {
    const c = new Collector();
    c.take("--incidence-angle-max", parseNumber(s.max, `${label}の上限`, true));
    return c.result();
  },
  intensity: (s, label) => {
    const c = new Collector();
    minMax(c, label, ["--intensity-min", s.min, "下限"], ["--intensity-max", s.max, "上限"]);
    return c.result();
  },
  range: (s, label) => {
    const c = new Collector();
    minMax(c, label, ["--range-min", s.min, "下限"], ["--range-max", s.max, "上限"]);
    return c.result();
  },
  deduplicate: () => {
    const c = new Collector();
    c.flag("--deduplicate");
    return c.result();
  },
  statistical: (s, label) => {
    const c = new Collector();
    c.flag("--outlier-statistical");
    c.take("--outlier-mean-k", parseCount(s.meanK, `${label}の近傍点数`));
    c.take("--outlier-multiplier", parsePositive(s.multiplier, `${label}の倍率`, true));
    return c.result();
  },
  radiusOutlier: (s, label) => {
    const c = new Collector();
    c.flag("--outlier-radius");
    c.take("--outlier-radius-value", parsePositive(s.radius, `${label}の半径`, true));
    c.take("--outlier-min-k", parseCount(s.minK, `${label}の最低近傍点数`));
    return c.result();
  },
  classes: (s, label) => {
    const c = new Collector();
    c.list("--keep-classes", parseIntList(s.codes, label, 0, 255));
    return c.result();
  },
  returns: (s, label) => {
    const c = new Collector();
    c.list("--keep-returns", parseIntList(s.numbers, label, 1, 15));
    return c.result();
  },
  clip: (s, label) => {
    const c = new Collector();
    const axes: ReadonlyArray<[string, string, Flag, string, Flag]> = [
      ["X", s.xmin, "--clip-xmin", s.xmax, "--clip-xmax"],
      ["Y", s.ymin, "--clip-ymin", s.ymax, "--clip-ymax"],
      ["Z", s.zmin, "--clip-zmin", s.zmax, "--clip-zmax"],
    ];
    for (const [axis, lo, loFlag, hi, hiFlag] of axes) {
      const a = c.take(loFlag, parseNumber(lo, `${label}の ${axis} 下限`, false));
      const b = c.take(hiFlag, parseNumber(hi, `${label}の ${axis} 上限`, false));
      if (a !== null && b !== null && a > b) c.error(`${label}: ${axis} の下限が上限より大きくなっています。`);
    }
    const all = [s.xmin, s.xmax, s.ymin, s.ymax, s.zmin, s.zmax];
    if (all.every((v) => v.trim() === "")) c.error(`${label}: 範囲を 1 つ以上入れてください。`);
    return c.result();
  },
  heightZ: (s, label) => {
    const c = new Collector();
    minMax(c, label, ["--z-min", s.min, "下限"], ["--z-max", s.max, "上限"]);
    return c.result();
  },
  sample: (s, label) => {
    const c = new Collector();
    c.take("--sample-radius", parsePositive(s.radius, `${label}の半径`, true));
    return c.result();
  },
  ground: (s, label) => {
    const c = new Collector();
    c.choice("--ground-classify", s.method);
    const cell = c.take("--ground-cell", parsePositive(s.cell, `${label}のグリッドサイズ`, false));
    if (s.method === "csf" && s.window.trim() !== "") {
      c.error(`${label}: csf では最大窓サイズを使いません。空にしてください。`);
    }
    const win =
      s.method === "csf" ? null : c.take("--ground-window", parsePositive(s.window, `${label}の最大窓サイズ`, false));
    c.take("--ground-threshold", parsePositive(s.threshold, `${label}の高さの閾値`, false));
    if (cell !== null && win !== null && win < cell) {
      c.error(`${label}: 最大窓サイズはグリッドサイズ以上にしてください。`);
    }
    if (s.groundOnly) c.flag("--ground-only");
    if (s.keepClasses) c.flag("--ground-keep-classes");
    return c.result();
  },
};

const ORDER: readonly SectionKey[] = [
  "incidence",
  "intensity",
  "range",
  "deduplicate",
  "statistical",
  "radiusOutlier",
  "classes",
  "returns",
  "clip",
  "heightZ",
  "sample",
  "ground",
];

function buildSection<K extends SectionKey>(key: K, form: FilterForm): SectionResult {
  const builder = BUILDERS[key] as (section: FilterForm[K], label: string) => SectionResult;
  return builder(form[key], SECTION_LABELS[key]);
}

/** Builds the filter arguments, or every problem found on the form. */
export function buildFilterArgs(form: FilterForm): BuildResult {
  const enabled = ORDER.filter((key) => form[key].enabled);
  if (enabled.length === 0) {
    return { ok: false, errors: [{ section: null, message: "フィルターを 1 つ以上選んでください。" }] };
  }
  const results = enabled.map((key) => ({ key, ...buildSection(key, form) }));
  const errors = results.flatMap((r) => r.errors.map((message) => ({ section: r.key, message })));
  if (errors.length > 0) return { ok: false, errors };
  return { ok: true, args: results.flatMap((r) => r.args) };
}
