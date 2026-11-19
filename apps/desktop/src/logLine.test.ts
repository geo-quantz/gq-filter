import { describe, expect, it } from "vitest";
import { isPluginNoise, logLineClass } from "./logLine";

const NOISE =
  "PDAL: Unable to load plugin /opt/pdal/lib/libpdal_plugin_reader_hdf.dylib: " +
  "dlopen(/opt/pdal/lib/libpdal_plugin_reader_hdf.dylib, 0x0001): Library not loaded: @rpath/libhdf5.dylib";

describe("isPluginNoise", () => {
  it("recognises PDAL plugin-load messages", () => {
    expect(isPluginNoise(NOISE)).toBe(true);
  });

  it("does not hide a real error glued onto the noise", () => {
    expect(isPluginNoise(`${NOISE}Pipeline execution failed: readers.las: bad header`)).toBe(false);
    expect(isPluginNoise(`${NOISE} Error: input file not found`)).toBe(false);
  });

  it("leaves other lines alone", () => {
    expect(isPluginNoise("Error: plugin option is wrong")).toBe(false);
    expect(isPluginNoise("Points processed: 10")).toBe(false);
  });
});

describe("logLineClass", () => {
  it("mutes plugin noise and keeps other stderr lines marked", () => {
    expect(logLineClass({ stream: "stderr", line: NOISE })).toBe("stderr noise");
    expect(logLineClass({ stream: "stderr", line: "Error: x" })).toBe("stderr");
    expect(logLineClass({ stream: "stdout", line: "Points processed: 10" })).toBeUndefined();
  });
});
