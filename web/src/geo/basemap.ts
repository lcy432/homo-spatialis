// 国家底图（Natural Earth，公有领域；来源与处理见 public/geo/SOURCES.md）。中文界面用中国立场版，英文界面用默认版。
// 按需加载（不进首屏包），每种语言只读一次。底图只说明“国家在哪里”，不是模拟范围。
import type { Feature, FeatureCollection, Geometry, MultiPolygon, Polygon } from "geojson";
import { geoArea } from "d3-geo";
import { feature } from "topojson-client";
import type { Topology } from "topojson-specification";
import type { Locale } from "../prefs";

export interface CountryProps {
  iso2: string | null;
  a3: string;
  name_zh: string | null;
  name_en: string | null;
}

export type CountryFeature = Feature<Geometry, CountryProps>;

const cache = new Map<Locale, Promise<CountryFeature[]>>();

export function loadCountries(locale: Locale): Promise<CountryFeature[]> {
  let p = cache.get(locale);
  if (!p) {
    p = fetch(`/geo/countries-${locale}.json`, { credentials: "same-origin" })
      .then((r) => {
        if (!r.ok) throw new Error(`basemap ${r.status}`);
        return r.json() as Promise<Topology>;
      })
      .then((topo) => {
        const fc = feature(topo, topo.objects.countries as never) as unknown as FeatureCollection<Geometry, CountryProps>;
        return fc.features;
      });
    p.catch(() => cache.delete(locale));
    cache.set(locale, p);
  }
  return p;
}

/** 一个国家面积最大的那块陆地（美国就是本土 48 州）：国家视图按它定范围，不把远处的领土挪进小框。 */
export function mainland(f: CountryFeature): Feature<Polygon> | null {
  const g = f.geometry;
  if (!g) return null;
  if (g.type === "Polygon") return { type: "Feature", properties: {}, geometry: g };
  if (g.type !== "MultiPolygon") return null;
  let best: Polygon | null = null;
  let bestArea = -1;
  for (const coords of (g as MultiPolygon).coordinates) {
    const poly: Polygon = { type: "Polygon", coordinates: coords };
    const a = geoArea(poly);
    if (a > bestArea) {
      bestArea = a;
      best = poly;
    }
  }
  return best ? { type: "Feature", properties: {}, geometry: best } : null;
}

export function countryName(f: CountryFeature, locale: Locale): string {
  return (locale === "zh" ? f.properties.name_zh : f.properties.name_en) ?? f.properties.name_en ?? f.properties.a3;
}
