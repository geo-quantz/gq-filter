import { describe, expect, it } from "vitest";
import { defaultFormat, describeCounts, parentDir } from "./paths";

describe("paths", () => {
  it("parentDir handles both separators", () => {
    expect(parentDir("/a/b/c.las")).toBe("/a/b");
    expect(parentDir("C:\\data\\c.laz")).toBe("C:\\data");
    expect(parentDir("/c.las")).toBe("/");
    expect(parentDir("c.las")).toBe("");
  });

  it("defaultFormat keeps LAS and compresses the rest", () => {
    expect(defaultFormat("/a/x.LAS")).toBe("las");
    expect(defaultFormat("/a/x.laz")).toBe("laz");
    expect(defaultFormat("/a/x.e57")).toBe("laz");
  });

  it("describeCounts shows the change", () => {
    expect(describeCounts(7388, 7262)).toBe("入力 7,388 点 → 出力 7,262 点（-126 点、-1.7%）");
    expect(describeCounts(null, 10)).toBe("出力 10 点");
    expect(describeCounts(0, 0)).toBe("入力 0 点 → 出力 0 点（0 点、0.0%）");
    expect(describeCounts(5, null)).toBe("");
  });
});
