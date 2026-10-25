// 地图 / 地球的位置：稿纸模式画平面地图，地球模式按需加载可拖动的写实地球；两者与城市列表共用同一份选择与详情状态。
// * 点亮的国家 = 能力清单里有可选城市的国家（输入）或本轮结果里的国家（输出）；点国家只筛选，不替用户选城。
// * 城市点只画 E3 给了可信显示锚点的城市。显示清单（GeographyDisplayBundle）还没交付：现在没有锚点，如实说明，列表是完整入口。
// * 没有 WebGL、地球加载失败时说明原因，退回平面地图，不阻断填写与结果。
import { Component, type ReactNode, Suspense, lazy, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { CityOption } from "../contract/types";
import { useMode } from "../ui/mode";
import { type MapAnchor, PaperMap } from "./maps/PaperMap";
import styles from "./maps/Maps.module.css";

const GlobeView = lazy(() => import("./maps/GlobeView"));

export interface CityMapProps {
  purpose: "input" | "output";
  cities: readonly CityOption[];
  /** 输出时本轮结果里的国家（输入时取能力清单里有可选城市的国家）。 */
  countries?: readonly string[];
  selected?: readonly string[];
  onToggle?(cityRef: string, on: boolean): void;
  onOpenCity?(cityRef: string): void;
  colors?: ReadonlyMap<string, string>;
  country: string | null;
  onCountry(country: string | null): void;
}

/** 城市的显示锚点：等 E3 的地理显示清单（需要可信的 WGS84 坐标）。没有交付之前一律为空，不猜坐标。 */
export function useCityAnchors(_cities: readonly CityOption[]): MapAnchor[] {
  return [];
}

export function webglAvailable(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return !!(canvas.getContext("webgl2") ?? canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

class GlobeBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

export function CityMapSlot(props: CityMapProps) {
  const { t } = useTranslation();
  const { mode } = useMode();
  const [hasGl] = useState(() => (typeof document === "undefined" ? false : webglAvailable()));
  const anchors = useCityAnchors(props.cities);
  const supported = useMemo(
    () => new Set(props.countries ?? props.cities.filter((c) => c.selectable).map((c) => c.country)),
    [props.countries, props.cities],
  );
  const selected = useMemo(() => new Set(props.selected ?? []), [props.selected]);
  const onCity = (ref: string) => {
    if (props.purpose === "output") props.onOpenCity?.(ref);
    else props.onToggle?.(ref, !selected.has(ref));
  };
  const paper = (focus: string | null | undefined) => (
    <PaperMap
      supported={supported}
      country={props.country}
      onCountry={props.onCountry}
      anchors={anchors}
      selected={selected}
      colors={props.colors}
      onCity={onCity}
      focus={focus}
    />
  );
  const flat =
    props.purpose === "output" && supported.size > 1 ? (
      <div className={`${styles.mapRow} ${styles.multi}`}>{[...supported].map((c) => <div key={c}>{paper(c)}</div>)}</div>
    ) : props.purpose === "output" ? (
      paper([...supported][0] ?? null)
    ) : (
      paper(undefined)
    );

  let body: ReactNode = flat;
  if (mode === "globe") {
    const fallback = (
      <>
        <p className="box warn" data-globe="unavailable">
          {t("maps.globeUnavailable")}
        </p>
        {flat}
      </>
    );
    body = hasGl ? (
      <GlobeBoundary fallback={fallback}>
        <Suspense fallback={<p className="muted">{t("maps.globeLoading")}</p>}>
          <GlobeView
            supported={supported}
            country={props.country}
            onCountry={props.onCountry}
            anchors={anchors}
            selected={selected}
            colors={props.colors}
            onCity={onCity}
          />
        </Suspense>
      </GlobeBoundary>
    ) : (
      fallback
    );
  }
  return (
    <div className="city-map" data-purpose={props.purpose}>
      {body}
      {anchors.length === 0 ? (
        <p className="small box note" data-anchors="pending">
          {t("maps.anchorsPending")}
        </p>
      ) : null}
    </div>
  );
}
