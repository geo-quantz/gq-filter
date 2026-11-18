// The filter form: one block per gqfilter filter, with the CLI's own options.

import type { FormError } from "../args/build";
import { SECTION_LABELS } from "../args/build";
import { GROUND_METHODS, type FilterForm, type FormPatch, type SectionKey } from "../args/form";

interface FieldSpec {
  readonly key: string;
  readonly label: string;
  readonly placeholder?: string;
  readonly wide?: boolean;
}

interface SectionSpec {
  readonly key: Exclude<SectionKey, "ground">;
  readonly help: string;
  readonly fields: readonly FieldSpec[];
}

const SECTIONS: readonly SectionSpec[] = [
  {
    key: "incidence",
    help: "--incidence-angle-max。法線から求めた入射角が上限を超える点を除く。",
    fields: [{ key: "max", label: "上限 (度)" }],
  },
  {
    key: "intensity",
    help: "--intensity-min / --intensity-max。反射強度の範囲で残す。",
    fields: [
      { key: "min", label: "下限" },
      { key: "max", label: "上限" },
    ],
  },
  {
    key: "range",
    help: "--range-min / --range-max。器械点（E57 は見出しから、それ以外は原点）からの距離で残す。",
    fields: [
      { key: "min", label: "下限" },
      { key: "max", label: "上限" },
    ],
  },
  { key: "deduplicate", help: "--deduplicate。XYZ が同じ点を 1 つにする。", fields: [] },
  {
    key: "statistical",
    help: "--outlier-statistical。近傍 k 点の平均距離が閾値を超える点を除く。",
    fields: [
      { key: "meanK", label: "近傍点数 k（既定 8）" },
      { key: "multiplier", label: "標準偏差の倍率（既定 2.0）" },
    ],
  },
  {
    key: "radiusOutlier",
    help: "--outlier-radius。半径 r の中に k 点未満しかない孤立点を除く。",
    fields: [
      { key: "radius", label: "探索半径 r（既定 1.0）" },
      { key: "minK", label: "最低近傍点数 k（既定 2）" },
    ],
  },
  {
    key: "classes",
    help: "--keep-classes。指定した分類コードの点だけ残す（2=地表 6=建物 7=ノイズ など）。",
    fields: [{ key: "codes", label: "分類コード（カンマか空白で区切る）", placeholder: "2, 6", wide: true }],
  },
  {
    key: "returns",
    help: "--keep-returns。指定したリターン番号の点だけ残す（1 = 1st リターン）。",
    fields: [{ key: "numbers", label: "リターン番号（カンマか空白で区切る）", placeholder: "1", wide: true }],
  },
  {
    key: "clip",
    help: "--clip-xmin 〜 --clip-zmax。境界ボックスの中だけ残す。空の値は制限しない。",
    fields: [
      { key: "xmin", label: "X 下限" },
      { key: "xmax", label: "X 上限" },
      { key: "ymin", label: "Y 下限" },
      { key: "ymax", label: "Y 上限" },
      { key: "zmin", label: "Z 下限" },
      { key: "zmax", label: "Z 上限" },
    ],
  },
  {
    key: "heightZ",
    help: "--z-min / --z-max。Z（標高）の範囲で残す。",
    fields: [
      { key: "min", label: "下限" },
      { key: "max", label: "上限" },
    ],
  },
  {
    key: "sample",
    help: "--sample-radius。ポアソンディスクで間引く。半径 R の中に 1 点（座標は変えない）。",
    fields: [{ key: "radius", label: "半径 R" }],
  },
];

interface Props {
  readonly form: FilterForm;
  readonly errors: readonly FormError[];
  readonly disabled: boolean;
  readonly onChange: (patch: FormPatch) => void;
}

function SectionErrors({ errors }: { readonly errors: readonly FormError[] }) {
  if (errors.length === 0) return null;
  return (
    <ul className="field-errors" role="alert">
      {errors.map((e) => (
        <li key={e.message}>{e.message}</li>
      ))}
    </ul>
  );
}

function TextField(props: {
  readonly label: string;
  readonly value: string;
  readonly placeholder?: string;
  readonly wide?: boolean;
  readonly disabled: boolean;
  readonly onChange: (value: string) => void;
}) {
  return (
    <label className={props.wide ? "field wide" : "field"}>
      <span>{props.label}</span>
      <input
        type="text"
        inputMode="decimal"
        value={props.value}
        placeholder={props.placeholder}
        disabled={props.disabled}
        onChange={(e) => props.onChange(e.target.value)}
      />
    </label>
  );
}

function GroundSection({ form, errors, disabled, onChange }: Props) {
  const g = form.ground;
  const set = (patch: Partial<FilterForm["ground"]>) => onChange({ ground: patch });
  return (
    <fieldset className={g.enabled ? "filter on" : "filter"}>
      <legend>
        <label>
          <input type="checkbox" checked={g.enabled} disabled={disabled} onChange={(e) => set({ enabled: e.target.checked })} />
          {SECTION_LABELS.ground}
        </label>
      </legend>
      <p className="help">
        --ground-classify。PDAL の smrf / pmf / csf で地表を Class 2 に分類する。既定では点は消さない。空の値は PDAL の既定。長さはデータの座標単位。
      </p>
      {g.enabled && (
        <>
          <div className="fields">
            <label className="field">
              <span>方式</span>
              <select
                value={g.method}
                disabled={disabled}
                onChange={(e) => {
                  const method = e.target.value as FilterForm["ground"]["method"];
                  set(method === "csf" ? { method, window: "" } : { method });
                }}
              >
                {GROUND_METHODS.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </label>
            <TextField label="グリッドサイズ" value={g.cell} disabled={disabled} onChange={(cell) => set({ cell })} />
            {g.method !== "csf" && (
              <TextField label="最大窓サイズ" value={g.window} disabled={disabled} onChange={(window) => set({ window })} />
            )}
            <TextField label="高さの閾値" value={g.threshold} disabled={disabled} onChange={(threshold) => set({ threshold })} />
          </div>
          <div className="checks">
            <label>
              <input
                type="checkbox"
                checked={g.groundOnly}
                disabled={disabled}
                onChange={(e) => set({ groundOnly: e.target.checked })}
              />
              地表（Class 2）だけ残す（--ground-only）
            </label>
            <label>
              <input
                type="checkbox"
                checked={g.keepClasses}
                disabled={disabled}
                onChange={(e) => set({ keepClasses: e.target.checked })}
              />
              分類前に既存の分類をリセットしない（--ground-keep-classes）
            </label>
          </div>
        </>
      )}
      <SectionErrors errors={errors.filter((e) => e.section === "ground")} />
    </fieldset>
  );
}

export function FilterSections(props: Props) {
  const { form, errors, disabled, onChange } = props;
  return (
    <div className="filters">
      {SECTIONS.map((spec) => {
        const section = form[spec.key] as unknown as Record<string, string | boolean>;
        const enabled = form[spec.key].enabled;
        return (
          <fieldset key={spec.key} className={enabled ? "filter on" : "filter"}>
            <legend>
              <label>
                <input
                  type="checkbox"
                  checked={enabled}
                  disabled={disabled}
                  onChange={(e) => onChange({ [spec.key]: { enabled: e.target.checked } })}
                />
                {SECTION_LABELS[spec.key]}
              </label>
            </legend>
            <p className="help">{spec.help}</p>
            {enabled && spec.fields.length > 0 && (
              <div className="fields">
                {spec.fields.map((f) => (
                  <TextField
                    key={f.key}
                    label={f.label}
                    value={String(section[f.key] ?? "")}
                    placeholder={f.placeholder}
                    wide={f.wide}
                    disabled={disabled}
                    onChange={(value) => onChange({ [spec.key]: { [f.key]: value } })}
                  />
                ))}
              </div>
            )}
            <SectionErrors errors={errors.filter((e) => e.section === spec.key)} />
          </fieldset>
        );
      })}
      <GroundSection {...props} />
    </div>
  );
}

