import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { FeatureCollection } from "geojson";
import { topology } from "topojson-server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PaperMap, placeLabels } from "../src/components/maps/PaperMap";
import { samplePayload } from "../src/contract/samples";
import type { GeographyDisplayBundle } from "../src/contract/types";
import { resolveAnchors } from "../src/geo/anchors";
import { ModeProvider } from "../src/ui/mode";

// 展示测试用的两个方块“国家”与一个虚构的点（不是真实城市的坐标，只检查画法与交互）
const square = (lon: number, lat: number, size: number) => ({
  type: "Polygon" as const,
  coordinates: [
    [
      [lon, lat],
      [lon, lat + size],
      [lon + size, lat + size],
      [lon + size, lat],
      [lon, lat],
    ],
  ],
});
const topo = topology({
  countries: {
    type: "FeatureCollection",
    features: [
      { type: "Feature", properties: { iso2: "US", a3: "USA", name_zh: "美国", name_en: "United States" }, geometry: square(-110, 30, 20) },
      { type: "Feature", properties: { iso2: "CA", a3: "CAN", name_zh: "加拿大", name_en: "Canada" }, geometry: square(-110, 52, 20) },
    ],
  } as FeatureCollection,
});

afterEach(() => vi.unstubAllGlobals());

describe("标签避让", () => {
  it("挤在一起的标签往下挪，锚点（原坐标）不动", () => {
    const pts = [
      { x: 100, y: 50 },
      { x: 120, y: 55 },
      { x: 300, y: 200 },
    ];
    const ys = placeLabels(pts, 18);
    expect(ys[0]).toBe(50);
    expect(ys[1]).toBe(68);
    expect(ys[2]).toBe(200);
    expect(pts[1]).toEqual({ x: 120, y: 55 });
  });
});

describe("稿纸平面地图", () => {
  it("支持的国家是可点、可用键盘的按钮，点了只回调筛选；不支持的国家只有轮廓", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(topo), { status: 200 })));
    const onCountry = vi.fn();
    const { container } = render(
      <ModeProvider initial="paper">
        <PaperMap supported={new Set(["US"])} country={null} onCountry={onCountry} anchors={[]} />
      </ModeProvider>,
    );
    const us = await waitFor(() => {
      const el = container.querySelector<HTMLElement>('[data-country="US"]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(us).toHaveAttribute("role", "button");
    expect(container.querySelector('[data-country="CA"]')).toBeNull();
    fireEvent.click(us);
    expect(onCountry).toHaveBeenCalledWith("US");
    fireEvent.keyDown(us, { key: "Enter" });
    expect(onCountry).toHaveBeenCalledTimes(2);
  });

  it("只画给了锚点的城市；锚点画在投影后的原位，点了回调打开这座城", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(topo), { status: 200 })));
    const onCity = vi.fn();
    render(
      <ModeProvider initial="paper">
        <PaperMap
          supported={new Set(["US"])}
          country={null}
          focus="US"
          onCountry={() => undefined}
          anchors={[{ cityRef: "X:TEST:1", label: "测试点", latitude: 40, longitude: -100, kind: "metro_internal_point" }]}
          onCity={onCity}
        />
      </ModeProvider>,
    );
    const dot = await screen.findByRole("button", { name: "测试点" });
    fireEvent.click(dot);
    expect(onCity).toHaveBeenCalledWith("X:TEST:1");
  });
});

describe("城市锚点只来自 E3 的地理显示清单", () => {
  it("真实六城（样例 123）：六座都有都会区内部点，按显示语言取短名；清单里没有的城市列为缺点", () => {
    const bundle = samplePayload<GeographyDisplayBundle>("123");
    const refs = (bundle.cities ?? []).map((c) => c.city_ref);
    const zh = resolveAnchors(bundle, [...refs, "US:CBSA2023:99999"], "zh");
    expect(zh.anchors).toHaveLength(6);
    expect(zh.bundleStatus).toBe("complete");
    const seattle = zh.anchors.find((a) => a.cityRef === "US:CBSA2023:42660");
    const city = bundle.cities?.find((c) => c.city_ref === "US:CBSA2023:42660");
    expect(seattle?.latitude).toBe(city?.anchor?.latitude);
    expect(seattle?.longitude).toBe(city?.anchor?.longitude);
    expect(seattle?.label).toBe(city?.names.zh.short);
    expect(resolveAnchors(bundle, refs, "en").anchors.find((a) => a.cityRef === "US:CBSA2023:42660")?.label).toBe(city?.names.en?.short);
    expect(zh.missing).toEqual([{ cityRef: "US:CBSA2023:99999", status: "not_in_bundle" }]);
  });

  it("合成资料包（样例 124）：合成城市没有坐标——不画点、不猜位置，原因是 not_applicable", () => {
    const bundle = samplePayload<GeographyDisplayBundle>("124");
    const refs = (bundle.cities ?? []).map((c) => c.city_ref);
    const r = resolveAnchors(bundle, refs, "zh");
    expect(r.anchors).toEqual([]);
    expect(r.missing.length).toBe(refs.length);
    expect(new Set(r.missing.map((m) => m.status))).toEqual(new Set(["not_applicable"]));
  });

  it("缺坐标夹具（样例 125）：芝加哥 unknown 不画，其他城照画；没读到清单时什么都不画也不报缺", () => {
    const bundle = samplePayload<GeographyDisplayBundle>("125");
    const refs = (bundle.cities ?? []).map((c) => c.city_ref);
    const r = resolveAnchors(bundle, refs, "zh");
    const chicago = bundle.cities?.find((c) => c.anchor_status === "unknown");
    expect(chicago).toBeTruthy();
    expect(r.missing).toEqual([{ cityRef: chicago?.city_ref, status: "unknown" }]);
    expect(r.anchors.map((a) => a.cityRef)).not.toContain(chicago?.city_ref);
    expect(r.anchors).toHaveLength(refs.length - 1);
    expect(resolveAnchors(undefined, refs, "zh")).toEqual({ anchors: [], missing: [], bundleStatus: null, resourceVersion: null });
  });
});
