// @vitest-environment node
// 提交的国家底图：每个多边形的绕行方向都要让 d3 读成“这一小块陆地”，不能读成“除它以外的整个地球”。
// 读反了的多边形在平面地图上铺满整个投影平面（2026-10-24 中文版的 ESB 就是这样，整页截图顶部一大块是空的）。
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { geoArea } from "d3-geo";
import type { FeatureCollection, MultiPolygon, Polygon } from "geojson";
import { feature } from "topojson-client";
import type { Topology } from "topojson-specification";
import { describe, expect, it } from "vitest";

function load(locale: "zh" | "en"): FeatureCollection<Polygon | MultiPolygon | null> {
  const file = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "public", "geo", `countries-${locale}.json`);
  const topo = JSON.parse(readFileSync(file, "utf-8")) as Topology;
  return feature(topo, topo.objects.countries as never) as unknown as FeatureCollection<Polygon | MultiPolygon | null>;
}

describe("国家底图", () => {
  it.each(["zh", "en"] as const)("%s：没有比半个地球还大的多边形（绕行方向都对）", (locale) => {
    const inverted: string[] = [];
    for (const f of load(locale).features) {
      const g = f.geometry;
      const polys = g?.type === "Polygon" ? [g.coordinates] : g?.type === "MultiPolygon" ? g.coordinates : [];
      for (const coordinates of polys) {
        if (geoArea({ type: "Polygon", coordinates }) > 2 * Math.PI) inverted.push(String(f.properties?.a3));
      }
    }
    expect(inverted).toEqual([]);
  });

  it.each(["zh", "en"] as const)("%s：美国有边界，带二位码与中英名称", (locale) => {
    const us = load(locale).features.find((f) => f.properties?.iso2 === "US");
    expect(us?.geometry).toBeTruthy();
    expect(us?.properties?.name_zh).toBeTruthy();
    expect(us?.properties?.name_en).toBeTruthy();
  });
});
