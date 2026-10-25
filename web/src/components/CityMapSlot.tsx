// 地图 / 地球的位置：稿纸模式画平面地图，地球模式按需加载可拖动的写实地球；两者与城市列表共用同一份选择与详情状态。
// * 点亮的国家 = 能力清单里有可选城市的国家（输入）或本轮结果里的国家（输出）；点国家只筛选，不替用户选城。
// * 城市点只画 E3 地理显示清单（GeographyDisplayBundle）里 anchor_status = available 的城市，两种外观用同一个锚点；
//   清单读不到、城市没有可靠点或是合成城市时不画、说明原因，列表永远是完整入口。
// * 没有 WebGL、地球加载失败时说明原因，退回平面地图，不阻断填写与结果。
import { Component, type ReactNode, Suspense, lazy, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { CityOption, GeographyDisplayBundle } from "../contract/types";
import { type AnchorResolution, resolveAnchors } from "../geo/anchors";
import { Zh } from "../i18n/biz";
import { useLocale } from "../i18n/index";
import { useMode } from "../ui/mode";
import { PaperMap } from "./maps/PaperMap";
import styles from "./maps/Maps.module.css";

const GlobeView = lazy(() => import("./maps/GlobeView"));

export interface CityMapProps {
  purpose: "input" | "output";
  cities: readonly CityOption[];
  /** 输出时本轮结果里的国家（输入时取能力清单里有可选城市的国家）。 */
  countries?: readonly string[];
  /** 输出时本轮涵盖的城市（只给这些城市画点）；输入时默认全部可选城市。 */
  cityRefs?: readonly string[];
  /** 地理显示清单：undefined = 还在读，null = 读不到。 */
  geography?: GeographyDisplayBundle | null;
  selected?: readonly string[];
  onToggle?(cityRef: string, on: boolean): void;
  onOpenCity?(cityRef: string): void;
  colors?: ReadonlyMap<string, string>;
  country: string | null;
  onCountry(country: string | null): void;
  /** 地球模式下是否把地图撑满（页面另有侧栏时）。 */
  fill?: boolean;
  /** 稿纸平面地图放大到哪个国家（null = 世界）；给了 onFocus 就显示“放大到 / 回到世界”的按钮。 */
  focus?: string | null;
  onFocus?(country: string | null): void;
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

/** 锚点来源与缺点说明：读不到清单、城市没有可靠点、合成城市，各说各的，不猜位置。 */
function AnchorNotes({ res, geography, cities }: { res: AnchorResolution; geography: GeographyDisplayBundle | null | undefined; cities: readonly CityOption[] }) {
  const { t } = useTranslation();
  const locale = useLocale();
  const name = (ref: string) => cities.find((c) => c.city_ref === ref)?.label ?? ref;
  if (geography === undefined) return <p className="small muted">{t("maps.anchorLoading")}</p>;
  if (geography === null) {
    return (
      <p className="small box note" data-anchors="unavailable">
        {t("maps.anchorUnavailable")}
      </p>
    );
  }
  const notes = geography.notes ?? [];
  return (
    <div className="small muted" data-anchors={res.anchors.length ? "available" : "none"}>
      {res.anchors.length ? <p>{t("maps.anchorNote", { version: res.resourceVersion ?? "" })}</p> : null}
      {notes.length ? (
        // 清单自带的来源与基准说明（E3 原文）：收起来，想核对的人再展开
        <details>
          <summary>{t("maps.anchorSources")}</summary>
          <ul>
            {notes.map((n, i) => (
              <li key={`${i}.${n.zh.slice(0, 20)}`}>{locale === "en" && n.en ? n.en : <Zh>{n.zh}</Zh>}</li>
            ))}
          </ul>
        </details>
      ) : null}
      {res.missing.length ? (
        <p>
          {t("maps.anchorMissing")}
          {res.missing.map((m, i) => (
            <span key={m.cityRef}>
              {i ? "；" : ""}
              <Zh>{name(m.cityRef)}</Zh>（{t(`maps.anchorStatus.${m.status}` as "maps.anchorStatus.unknown")}）
            </span>
          ))}
        </p>
      ) : null}
    </div>
  );
}

export function CityMapSlot(props: CityMapProps) {
  const { t } = useTranslation();
  const locale = useLocale();
  const { mode } = useMode();
  const [hasGl] = useState(() => (typeof document === "undefined" ? false : webglAvailable()));
  const refs = useMemo(
    () => props.cityRefs ?? props.cities.filter((c) => props.purpose === "output" || c.selectable).map((c) => c.city_ref),
    [props.cityRefs, props.cities, props.purpose],
  );
  const resolution = useMemo(() => resolveAnchors(props.geography, refs, locale), [props.geography, refs, locale]);
  const anchors = resolution.anchors;
  const supported = useMemo(
    () => new Set(props.countries ?? props.cities.filter((c) => c.selectable).map((c) => c.country)),
    [props.countries, props.cities],
  );
  const selected = useMemo(() => new Set(props.selected ?? []), [props.selected]);
  const onCity = (ref: string) => {
    if (props.purpose === "output") props.onOpenCity?.(ref);
    else props.onToggle?.(ref, !selected.has(ref));
  };
  const countryLabel = (code: string) => {
    const g = props.geography?.cities?.find((c) => c.country === code);
    return (locale === "en" ? g?.country_names.en : null) ?? g?.country_names.zh ?? code;
  };
  const tools =
    props.onFocus && mode !== "globe" ? (
      <div className={styles.mapTools} role="group" aria-label={t("maps.zoomGroup")}>
        <button type="button" aria-pressed={props.focus == null} onClick={() => props.onFocus?.(null)}>
          {t("maps.world")}
        </button>
        {[...supported].map((c) => (
          <button key={c} type="button" aria-pressed={props.focus === c} onClick={() => props.onFocus?.(c)}>
            {t("maps.zoomTo", { country: countryLabel(c) })}
          </button>
        ))}
      </div>
    ) : null;
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
      purpose={props.purpose}
    />
  );
  const flat =
    props.purpose === "output" && supported.size > 1 ? (
      <div className={`${styles.mapRow} ${styles.multi}`}>{[...supported].map((c) => <div key={c}>{paper(c)}</div>)}</div>
    ) : props.purpose === "output" ? (
      paper([...supported][0] ?? null)
    ) : (
      paper(props.onFocus ? (props.focus ?? null) : undefined)
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
            purpose={props.purpose}
            fill={props.fill}
          />
        </Suspense>
      </GlobeBoundary>
    ) : (
      fallback
    );
  }
  const notes = <AnchorNotes res={resolution} geography={props.geography} cities={props.cities} />;
  const filling = props.fill && mode === "globe" && hasGl;
  return (
    <div className={`city-map ${filling ? "city-map-fill" : ""}`} data-purpose={props.purpose} data-anchor-count={anchors.length}>
      {tools}
      {body}
      {filling ? (
        <details className={styles.stageNotes}>
          <summary>{t("maps.aboutMap")}</summary>
          <p className="small muted">
            {t("maps.globeHint")} {t("maps.textureCredit")}
          </p>
          {notes}
        </details>
      ) : (
        notes
      )}
    </div>
  );
}
