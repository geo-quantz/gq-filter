import { describe, expect, it } from "vitest";
import cases from "./cases.json";
import { buildFilterArgs, CLI_FLAGS } from "./build";
import { applyPatch, DEFAULT_FORM, type FormPatch } from "./form";

function build(patch: FormPatch) {
  return buildFilterArgs(applyPatch(DEFAULT_FORM, patch));
}

function errorsOf(patch: FormPatch) {
  const result = build(patch);
  if (result.ok) throw new Error(`expected errors, got ${result.args.join(" ")}`);
  return result.errors;
}

describe("buildFilterArgs: shared cases (also parsed by cli.py in tests/test_desktop_args.py)", () => {
  for (const c of cases.cases) {
    it(c.name, () => {
      expect(build(c.form as FormPatch)).toEqual({ ok: true, args: c.argv });
    });
  }

  it("every flag the app can emit appears in a shared case", () => {
    const used = new Set(cases.cases.flatMap((c) => c.argv.filter((t) => t.startsWith("--"))));
    expect([...CLI_FLAGS].sort()).toEqual([...used].sort());
  });
});

describe("buildFilterArgs: validation", () => {
  it("asks for at least one filter", () => {
    expect(errorsOf({})).toEqual([{ section: null, message: "フィルターを 1 つ以上選んでください。" }]);
  });

  it("rejects text that is not a number", () => {
    const errors = errorsOf({ intensity: { enabled: true, min: "abc" } });
    expect(errors).toEqual([{ section: "intensity", message: "強度の下限: 数で入れてください。" }]);
  });

  it("requires a value for a range filter", () => {
    expect(errorsOf({ heightZ: { enabled: true } })[0]?.section).toBe("heightZ");
    expect(errorsOf({ clip: { enabled: true } })[0]?.section).toBe("clip");
    expect(errorsOf({ incidence: { enabled: true } })[0]?.section).toBe("incidence");
    expect(errorsOf({ sample: { enabled: true } })[0]?.section).toBe("sample");
  });

  it("rejects min greater than max", () => {
    const errors = errorsOf({ heightZ: { enabled: true, min: "10", max: "5" } });
    expect(errors[0]?.message).toContain("下限が上限より大きく");
  });

  it("rejects non-positive radius and k", () => {
    expect(errorsOf({ sample: { enabled: true, radius: "0" } })[0]?.section).toBe("sample");
    expect(errorsOf({ statistical: { enabled: true, meanK: "1.5" } })[0]?.section).toBe("statistical");
    expect(errorsOf({ radiusOutlier: { enabled: true, minK: "0" } })[0]?.section).toBe("radiusOutlier");
    expect(errorsOf({ statistical: { enabled: true, multiplier: "-1" } })[0]?.section).toBe("statistical");
  });

  it("checks class codes and return numbers", () => {
    expect(errorsOf({ classes: { enabled: true, codes: "" } })[0]?.section).toBe("classes");
    expect(errorsOf({ classes: { enabled: true, codes: "2, x" } })[0]?.section).toBe("classes");
    expect(errorsOf({ classes: { enabled: true, codes: "256" } })[0]?.section).toBe("classes");
    expect(errorsOf({ returns: { enabled: true, numbers: "0" } })[0]?.section).toBe("returns");
    expect(errorsOf({ returns: { enabled: true, numbers: "16" } })[0]?.section).toBe("returns");
  });

  it("checks ground tuning like the CLI does", () => {
    expect(errorsOf({ ground: { enabled: true, method: "csf", window: "10" } })[0]?.message).toContain("csf");
    expect(errorsOf({ ground: { enabled: true, method: "smrf", cell: "-1" } })[0]?.section).toBe("ground");
    expect(errorsOf({ ground: { enabled: true, method: "pmf", cell: "5", window: "2" } })[0]?.message).toContain(
      "窓",
    );
  });

  it("rejects infinity and empty-looking numbers", () => {
    expect(errorsOf({ heightZ: { enabled: true, min: "Infinity" } })[0]?.section).toBe("heightZ");
    expect(errorsOf({ heightZ: { enabled: true, min: "   " } })[0]?.section).toBe("heightZ");
  });

  it("normalises numbers it passes on", () => {
    expect(build({ sample: { enabled: true, radius: " 0.50 " } })).toEqual({
      ok: true,
      args: ["--sample-radius", "0.5"],
    });
  });

  it("does not change the form it is given", () => {
    const form = applyPatch(DEFAULT_FORM, { classes: { enabled: true, codes: "2" } });
    const copy = JSON.parse(JSON.stringify(form));
    buildFilterArgs(form);
    expect(form).toEqual(copy);
  });
});
