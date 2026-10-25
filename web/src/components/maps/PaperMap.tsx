// 稿纸模式的平面地图（React 管 SVG，d3-geo 只负责投影与路径）。
// * 投影：世界图用等面积的 Equal Earth；国家图用以该国为中心的圆锥等面积投影，范围取面积最大的那块陆地（不把远处领土挪进小框）。
//   铅笔感只来自双层描边与填色纹理，不移动任何顶点，不改变面积关系或位置。
// * 支持的国家涂色、可以点（只筛选城市，不替用户选城）；其余国家只有轮廓。
// * 城市点只画 E3 给了可信显示锚点的城市；锚点永远画在原位，标签避让只移动文字并画引线。没有锚点的城市照样在列表里。
import { geoConicEqualArea, geoEqualEarth, geoGraticule10, geoPath, type GeoProjection } from "d3-geo";
import { type KeyboardEvent, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { type CountryFeature, countryName, loadCountries, mainland } from "../../geo/basemap";
import { useLocale } from "../../i18n/index";
import styles from "./Maps.module.css";

export interface MapAnchor {
  cityRef: string;
  label: string;
  latitude: number;
  longitude: number;
}

export interface PaperMapProps {
  supported: ReadonlySet<string>;
  country: string | null;
  onCountry(country: string | null): void;
  anchors: readonly MapAnchor[];
  selected?: ReadonlySet<string>;
  colors?: ReadonlyMap<string, string>;
  onCity?(cityRef: string): void;
  /** 输出时每个国家各画一张（多国并列）；输入时 null 表示世界图。 */
  focus?: string | null;
}

const W = 960;
const H = 520;

function projectionFor(features: readonly CountryFeature[], focus: string | null): GeoProjection {
  const f = focus ? features.find((x) => x.properties.iso2 === focus) : null;
  const land = f ? mainland(f) : null;
  if (land) {
    const [[x0, y0], [x1, y1]] = geoPath().bounds(land) as [[number, number], [number, number]];
    const lonC = (x0 + x1) / 2;
    const span = y1 - y0;
    return geoConicEqualArea()
      .parallels([y0 + span / 6, y1 - span / 6])
      .rotate([-lonC, 0])
      .fitExtent(
        [
          [16, 16],
          [W - 16, H - 16],
        ],
        land,
      );
  }
  return geoEqualEarth().fitExtent(
    [
      [8, 8],
      [W - 8, H - 8],
    ],
    { type: "Sphere" },
  );
}

/** 标签避让：按纵坐标排开，挤在一起的往下挪；锚点不动，挪过的标签画引线。 */
export function placeLabels(points: { x: number; y: number }[], minGap = 18): number[] {
  const order = points.map((p, i) => ({ i, y: p.y })).sort((a, b) => a.y - b.y);
  const ys = points.map((p) => p.y);
  let last = Number.NEGATIVE_INFINITY;
  for (const o of order) {
    const y = Math.max(o.y, last + minGap);
    ys[o.i] = y;
    last = y;
  }
  return ys;
}

export function PaperMap(props: PaperMapProps) {
  const { t } = useTranslation();
  const locale = useLocale();
  const [features, setFeatures] = useState<CountryFeature[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setFailed(false);
    loadCountries(locale).then(
      (fs) => alive && setFeatures(fs),
      () => alive && setFailed(true),
    );
    return () => {
      alive = false;
    };
  }, [locale]);

  const focus = props.focus !== undefined ? props.focus : props.country;
  const projection = useMemo(() => (features ? projectionFor(features, focus) : null), [features, focus]);
  const path = useMemo(() => (projection ? geoPath(projection) : null), [projection]);

  if (failed) return <p className="box warn">{t("maps.basemapFailed")}</p>;
  if (!features || !projection || !path) return <p className="muted">{t("common.loading")}</p>;

  const onKey = (code: string) => (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      props.onCountry(props.country === code ? null : code);
    }
  };
  const projected = props.anchors
    .map((a) => {
      const p = projection([a.longitude, a.latitude]);
      return p ? { a, x: p[0], y: p[1] } : null;
    })
    .filter((p): p is { a: MapAnchor; x: number; y: number } => p !== null && p.x >= 0 && p.x <= W && p.y >= 0 && p.y <= H);
  const labelYs = placeLabels(projected);
  const title = focus ? t("maps.countryMap", { country: focus }) : t("maps.worldMap");

  return (
    <figure className={styles.paperMap} data-map="paper" data-focus={focus ?? "world"}>
      <svg viewBox={`0 0 ${W} ${H}`} role="group" aria-label={title} className={styles.svg}>
        <defs>
          <pattern id="pencil-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(35)">
            <line x1="0" y1="0" x2="0" y2="6" className={styles.hatchLine} />
          </pattern>
        </defs>
        {!focus ? <path d={path({ type: "Sphere" }) ?? ""} className={styles.sphere} /> : null}
        <path d={path(geoGraticule10()) ?? ""} className={styles.graticule} />
        {features.map((f) => {
          const code = f.properties.iso2;
          const on = !!code && props.supported.has(code);
          const d = path(f) ?? "";
          if (!d) return null;
          if (!on) return <path key={f.properties.a3} d={d} className={styles.country} />;
          const active = props.country === code;
          return (
            <g
              key={f.properties.a3}
              role="button"
              tabIndex={0}
              aria-pressed={active}
              aria-label={t("maps.countryButton", { country: countryName(f, locale) })}
              data-country={code}
              className={styles.supportedGroup}
              onClick={() => props.onCountry(active ? null : code)}
              onKeyDown={onKey(code)}
            >
              <path d={d} className={styles.supportedFill} />
              <path d={d} className={styles.supportedLine} />
              <path d={d} className={styles.supportedLine2} />
            </g>
          );
        })}
        {projected.map(({ a, x, y }, i) => {
          const ly = labelYs[i] as number;
          const moved = Math.abs(ly - y) > 0.5;
          const color = props.colors?.get(a.cityRef);
          const sel = props.selected?.has(a.cityRef);
          return (
            <g key={a.cityRef} data-anchor={a.cityRef} className={styles.anchorGroup}>
              {moved ? <line x1={x} y1={y} x2={x + 10} y2={ly} className={styles.leader} /> : null}
              <circle
                cx={x}
                cy={y}
                r={7}
                className={sel ? styles.anchorSelected : styles.anchor}
                style={color ? { fill: color } : undefined}
                role={props.onCity ? "button" : undefined}
                tabIndex={props.onCity ? 0 : undefined}
                aria-label={a.label}
                onClick={() => props.onCity?.(a.cityRef)}
                onKeyDown={(e) => {
                  if (props.onCity && (e.key === "Enter" || e.key === " ")) {
                    e.preventDefault();
                    props.onCity(a.cityRef);
                  }
                }}
              />
              <text x={x + 12} y={ly + 4} className={styles.label}>
                {a.label}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="small muted">{t("maps.basemapNote")}</figcaption>
    </figure>
  );
}
