// 城市在地图与地球上的锚点：只来自 E3 的地理显示清单（GeographyDisplayBundle），不按城市名查坐标、不硬编码。
// * 清单按资料包指纹读；每座城的 city_ref 自带地理方案与版本（例如 US:CBSA2023:42660），锚点只配给同一个 city_ref。
// * 只有 anchor_status = available 的城市画点；unknown（真实城市但没有可靠点）与 not_applicable（合成城市）不画，列出原因。
// * 六城的点是 Census 都会区的内部点（代表点）：不是住处、主城市中心或都会区边界。两种外观用同一个锚点。
import type { CityGeography, GeographyDisplayBundle } from "../contract/types";
import type { Locale } from "../prefs";

export interface MapAnchor {
  cityRef: string;
  label: string;
  latitude: number;
  longitude: number;
  kind: "principal_city_point" | "metro_internal_point";
}

export interface AnchorResolution {
  anchors: MapAnchor[];
  /** 本轮要显示、清单里却没有可用锚点的城市（按城市编号），附清单给的状态。 */
  missing: { cityRef: string; status: CityGeography["anchor_status"] | "not_in_bundle" }[];
  /** 清单整体状态；null 表示还没有读到清单。 */
  bundleStatus: GeographyDisplayBundle["status"] | null;
  resourceVersion: string | null;
}

/** 城市在当前显示语言下的短名；英文名没有时用中文短名（不翻译）。 */
export function cityShortName(c: CityGeography, locale: Locale): string {
  return (locale === "en" ? c.names.en?.short : null) ?? c.names.zh.short;
}

export function resolveAnchors(
  bundle: GeographyDisplayBundle | null | undefined,
  cityRefs: readonly string[],
  locale: Locale,
): AnchorResolution {
  if (!bundle) return { anchors: [], missing: [], bundleStatus: null, resourceVersion: null };
  const byRef = new Map((bundle.cities ?? []).map((c) => [c.city_ref, c] as const));
  const anchors: MapAnchor[] = [];
  const missing: AnchorResolution["missing"] = [];
  for (const ref of cityRefs) {
    const c = byRef.get(ref);
    if (!c) {
      missing.push({ cityRef: ref, status: "not_in_bundle" });
      continue;
    }
    if (c.anchor_status !== "available" || !c.anchor) {
      missing.push({ cityRef: ref, status: c.anchor_status });
      continue;
    }
    anchors.push({
      cityRef: ref,
      label: cityShortName(c, locale),
      latitude: c.anchor.latitude,
      longitude: c.anchor.longitude,
      kind: c.anchor.kind,
    });
  }
  return { anchors, missing, bundleStatus: bundle.status, resourceVersion: bundle.resource_version };
}
