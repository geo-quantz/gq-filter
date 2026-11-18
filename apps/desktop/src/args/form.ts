// The values on the filter form. Every field is the raw text of an input box,
// so the builder can report what is wrong with it instead of losing it.
// The defaults are the defaults of the gqfilter CLI (cli.py).

export const GROUND_METHODS = ["smrf", "pmf", "csf"] as const;
export type GroundMethod = (typeof GROUND_METHODS)[number];

export interface FilterForm {
  readonly incidence: { readonly enabled: boolean; readonly max: string };
  readonly intensity: { readonly enabled: boolean; readonly min: string; readonly max: string };
  readonly range: { readonly enabled: boolean; readonly min: string; readonly max: string };
  readonly deduplicate: { readonly enabled: boolean };
  readonly statistical: { readonly enabled: boolean; readonly meanK: string; readonly multiplier: string };
  readonly radiusOutlier: { readonly enabled: boolean; readonly radius: string; readonly minK: string };
  readonly classes: { readonly enabled: boolean; readonly codes: string };
  readonly returns: { readonly enabled: boolean; readonly numbers: string };
  readonly clip: {
    readonly enabled: boolean;
    readonly xmin: string;
    readonly xmax: string;
    readonly ymin: string;
    readonly ymax: string;
    readonly zmin: string;
    readonly zmax: string;
  };
  readonly heightZ: { readonly enabled: boolean; readonly min: string; readonly max: string };
  readonly sample: { readonly enabled: boolean; readonly radius: string };
  readonly ground: {
    readonly enabled: boolean;
    readonly method: GroundMethod;
    readonly cell: string;
    readonly window: string;
    readonly threshold: string;
    readonly groundOnly: boolean;
    readonly keepClasses: boolean;
  };
}

export type SectionKey = keyof FilterForm;

export const DEFAULT_FORM: FilterForm = {
  incidence: { enabled: false, max: "" },
  intensity: { enabled: false, min: "", max: "" },
  range: { enabled: false, min: "", max: "" },
  deduplicate: { enabled: false },
  statistical: { enabled: false, meanK: "8", multiplier: "2.0" },
  radiusOutlier: { enabled: false, radius: "1.0", minK: "2" },
  classes: { enabled: false, codes: "" },
  returns: { enabled: false, numbers: "" },
  clip: { enabled: false, xmin: "", xmax: "", ymin: "", ymax: "", zmin: "", zmax: "" },
  heightZ: { enabled: false, min: "", max: "" },
  sample: { enabled: false, radius: "" },
  ground: {
    enabled: false,
    method: "csf",
    cell: "",
    window: "",
    threshold: "",
    groundOnly: false,
    keepClasses: false,
  },
};

/** A partial form, section by section (used by tests and by `withSection`). */
export type FormPatch = { readonly [K in SectionKey]?: Partial<FilterForm[K]> };

/** Returns a new form with the patch applied; the original is not changed. */
export function applyPatch(form: FilterForm, patch: FormPatch): FilterForm {
  const next: Record<string, unknown> = { ...form };
  for (const key of Object.keys(patch) as SectionKey[]) {
    next[key] = { ...form[key], ...patch[key] };
  }
  return next as unknown as FilterForm;
}
