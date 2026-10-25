import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { FeatureCollection } from "geojson";
import { topology } from "topojson-server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PaperMap, placeLabels } from "../src/components/maps/PaperMap";
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
          anchors={[{ cityRef: "X:TEST:1", label: "测试点", latitude: 40, longitude: -100 }]}
          onCity={onCity}
        />
      </ModeProvider>,
    );
    const dot = await screen.findByRole("button", { name: "测试点" });
    fireEvent.click(dot);
    expect(onCity).toHaveBeenCalledWith("X:TEST:1");
  });
});
