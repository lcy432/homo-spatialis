// 写实地球（react-globe.gl / three.js，按需加载）：NASA Blue Marble 纹理、Natural Earth 国界（与平面地图同一份底图与语言立场）。
// * 拖动旋转、滚轮或双指缩放；不自动旋转。系统要求减少动态效果时，视角直接到位（不做过渡动画）。
// * 支持的国家高亮、可以点（只筛选城市，不替用户选城）；不支持的国家只有细轮廓。城市点只画 E3 给了可信锚点的城市。
// * 背面的点被地球挡住，点不中；拖动结束不算点击（由 three-render-objects 区分拖动与点击）。
// * 开发与测试构建里暴露 window.__E5_GLOBE__（读写视角），测试按视角数值判断“拖动改变了视角”，不比较像素。
import { geoCentroid } from "d3-geo";
import { useEffect, useMemo, useRef, useState } from "react";
import Globe, { type GlobeMethods } from "react-globe.gl";
import { useTranslation } from "react-i18next";
import { type CountryFeature, countryName, loadCountries, mainland } from "../../geo/basemap";
import { useLocale } from "../../i18n/index";
import type { MapAnchor } from "../../geo/anchors";
import styles from "./Maps.module.css";

export interface GlobeViewProps {
  supported: ReadonlySet<string>;
  country: string | null;
  onCountry(country: string | null): void;
  anchors: readonly MapAnchor[];
  selected?: ReadonlySet<string>;
  colors?: ReadonlyMap<string, string>;
  onCity?(cityRef: string): void;
  purpose?: "input" | "output";
  /** 页面另有侧栏时把地球撑满可用高度。 */
  fill?: boolean;
}

declare global {
  interface Window {
    __E5_GLOBE__?: { pov(): { lat: number; lng: number; altitude: number }; setPov(p: { lat: number; lng: number; altitude: number }): void };
  }
}

// 总览视角：对准现在能选的国家（各国大陆部分质心在球面上的平均方向）；这些国家分散在地球两侧、平均方向没有意义时，退回固定的大西洋视角。
const FALLBACK_HOME = { lat: 25, lng: -40, altitude: 2.4 };
const HOME_ALTITUDE = 1.9;
const RAD = Math.PI / 180;

function homeFor(features: readonly CountryFeature[] | null, supported: ReadonlySet<string>) {
  let x = 0;
  let y = 0;
  let z = 0;
  let n = 0;
  for (const f of features ?? []) {
    const code = f.properties.iso2;
    const land = code && supported.has(code) ? mainland(f) : null;
    if (!land) continue;
    const [lng, lat] = geoCentroid(land);
    x += Math.cos(lat * RAD) * Math.cos(lng * RAD);
    y += Math.cos(lat * RAD) * Math.sin(lng * RAD);
    z += Math.sin(lat * RAD);
    n += 1;
  }
  const r = Math.hypot(x, y, z);
  if (!n || r / n < 0.5) return FALLBACK_HOME;
  return { lat: Math.asin(z / r) / RAD, lng: Math.atan2(y, x) / RAD, altitude: HOME_ALTITUDE };
}

function reducedMotion(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

export default function GlobeView(props: GlobeViewProps) {
  const { t } = useTranslation();
  const locale = useLocale();
  const ref = useRef<GlobeMethods | undefined>(undefined);
  const wrap = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 640, h: 400 });
  const [features, setFeatures] = useState<CountryFeature[] | null>(null);

  useEffect(() => {
    let alive = true;
    loadCountries(locale).then((fs) => alive && setFeatures(fs), () => alive && setFeatures([]));
    return () => {
      alive = false;
    };
  }, [locale]);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      if (!entry) return;
      const w = Math.round(entry.contentRect.width);
      // 铺满舞台时高度就是容器的高度；普通版面按宽高比，最高不超过视口的七成
      const h = props.fill ? Math.round(entry.contentRect.height) : Math.round(Math.min(entry.contentRect.height || w * 0.62, window.innerHeight * 0.7));
      setSize({ w, h: Math.max(h, 120) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [props.fill]);

  const motionMs = reducedMotion() ? 0 : 700;
  const home = useMemo(() => homeFor(features, props.supported), [features, props.supported]);

  const focusPov = useMemo(() => {
    if (!features || !props.country) return null;
    const f = features.find((x) => x.properties.iso2 === props.country);
    const land = f ? mainland(f) : null;
    if (!land) return null;
    const [lng, lat] = geoCentroid(land);
    return { lat, lng, altitude: 1.25 };
  }, [features, props.country]);

  useEffect(() => {
    const g = ref.current;
    if (!g) return;
    const controls = g.controls() as { autoRotate?: boolean; enableDamping?: boolean };
    controls.autoRotate = false;
    if (import.meta.env.DEV) {
      window.__E5_GLOBE__ = {
        pov: () => g.pointOfView() as { lat: number; lng: number; altitude: number },
        setPov: (p) => g.pointOfView(p, 0),
      };
    }
    return () => {
      if (import.meta.env.DEV) delete window.__E5_GLOBE__;
    };
  }, [features]);

  useEffect(() => {
    if (focusPov) ref.current?.pointOfView(focusPov, motionMs);
  }, [focusPov, motionMs]);

  const polygons = features ?? [];
  const isOn = (f: object) => {
    const code = (f as CountryFeature).properties.iso2;
    return !!code && props.supported.has(code);
  };

  return (
    <div data-map="globe" className={props.fill ? styles.globeRootFill : styles.globeRoot}>
      <div className={`${styles.globeToolbar} ${props.fill ? styles.globeToolbarFill : ""}`} role="toolbar" aria-label={t("app.mode.globe")}>
        <button type="button" onClick={() => ref.current?.pointOfView(home, motionMs)}>
          {t("maps.globeReset")}
        </button>
        <button type="button" disabled={!focusPov} onClick={() => focusPov && ref.current?.pointOfView(focusPov, motionMs)}>
          {t("maps.globeFocus")}
        </button>
      </div>
      <div ref={wrap} className={`${styles.globeWrap} ${props.fill ? styles.globeWrapFill : ""}`} aria-label={t("maps.globeHint")} role="img">
        {features ? (
          <Globe
            ref={ref}
            width={size.w}
            height={size.h}
            backgroundColor="#070b10"
            globeImageUrl="/geo/earth-blue-marble-4096.jpg"
            showAtmosphere
            atmosphereColor="#9ccfe0"
            atmosphereAltitude={0.12}
            animateIn={false}
            polygonsData={polygons}
            polygonGeoJsonGeometry="geometry"
            polygonCapColor={(f: object) => (isOn(f) ? "rgba(98, 191, 214, 0.32)" : "rgba(0, 0, 0, 0)")}
            polygonSideColor={() => "rgba(0, 0, 0, 0)"}
            polygonStrokeColor={(f: object) => (isOn(f) ? "#8fe3f5" : "rgba(255, 255, 255, 0.28)")}
            polygonAltitude={(f: object) => (isOn(f) ? 0.012 : 0.004)}
            polygonLabel={(f: object) => (isOn(f) ? countryName(f as CountryFeature, locale) : "")}
            polygonsTransitionDuration={0}
            onPolygonClick={(f: object) => {
              const code = (f as CountryFeature).properties.iso2;
              if (code && props.supported.has(code)) props.onCountry(props.country === code ? null : code);
            }}
            pointsData={[...props.anchors]}
            pointLat={(p: object) => (p as MapAnchor).latitude}
            pointLng={(p: object) => (p as MapAnchor).longitude}
            pointColor={(p: object) => props.colors?.get((p as MapAnchor).cityRef) ?? (props.selected?.has((p as MapAnchor).cityRef) ? "#ffd36e" : "#ffffff")}
            pointAltitude={0.02}
            pointRadius={0.35}
            pointLabel={(p: object) => (p as MapAnchor).label}
            onPointClick={(p: object) => props.onCity?.((p as MapAnchor).cityRef)}
            labelsData={[...props.anchors]}
            labelLat={(p: object) => (p as MapAnchor).latitude}
            labelLng={(p: object) => (p as MapAnchor).longitude}
            labelText={(p: object) => (p as MapAnchor).label}
            labelSize={0.9}
            labelDotRadius={0}
            labelAltitude={0.025}
            labelColor={() => "rgba(233, 244, 251, 0.92)"}
            labelResolution={2}
            labelsTransitionDuration={0}
            onLabelClick={(p: object) => props.onCity?.((p as MapAnchor).cityRef)}
            onGlobeReady={() => ref.current?.pointOfView(focusPov ?? home, 0)}
          />
        ) : (
          <p className="muted" style={{ padding: "1em" }}>
            {t("maps.globeLoading")}
          </p>
        )}
      </div>
      {props.fill ? null : (
        <p className={`small muted ${styles.globeHint}`}>
          {t("maps.globeHint")} {t("maps.textureCredit")}
        </p>
      )}
    </div>
  );
}
