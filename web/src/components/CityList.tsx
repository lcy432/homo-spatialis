// 城市列表：完整的等价操作入口（地图与地球只是另一种入口，消费同一份选择）。国家筛选只改显示的范围，不改已选集合；
// “选择全部可用城市 / 清除”是明确的按钮。城市名称、说明与资料性质都是 E3 能力清单的原话。
import { useTranslation } from "react-i18next";
import type { CityOption } from "../contract/types";
import { Zh } from "../i18n/biz";

export function countriesOf(cities: readonly CityOption[]): string[] {
  return [...new Set(cities.map((c) => c.country))];
}

export function CityList({
  cities,
  selected,
  onToggle,
  onSetAll,
  country,
  onCountry,
  focusedCity,
}: {
  cities: readonly CityOption[];
  selected: readonly string[];
  onToggle(cityRef: string, on: boolean): void;
  onSetAll(cityRefs: string[]): void;
  country: string | null;
  onCountry(country: string | null): void;
  focusedCity?: string | null;
}) {
  const { t } = useTranslation();
  const countries = countriesOf(cities);
  const shown = cities.filter((c) => country === null || c.country === country);
  const selectable = cities.filter((c) => c.selectable).map((c) => c.city_ref);
  return (
    <div className="city-list" data-field="cities">
      {countries.length > 1 ? (
        <p>
          <label htmlFor="country-filter">{t("place.countryFilter")}</label>{" "}
          <select id="country-filter" value={country ?? ""} onChange={(e) => onCountry(e.target.value || null)}>
            <option value="">{t("place.allCountries")}</option>
            {countries.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </p>
      ) : null}
      <div className="row">
        <button type="button" onClick={() => onSetAll(selectable)}>
          {t("place.selectAll")}
        </button>
        <button type="button" onClick={() => onSetAll([])}>
          {t("place.clear")}
        </button>
        <span className="small" role="status" aria-live="polite">
          {t("place.selected", { n: selected.length })}
        </span>
      </div>
      <fieldset>
        <legend className="visually-hidden">{t("place.title")}</legend>
        {shown.map((c) => (
          <label
            key={c.city_ref}
            className={`choice top${focusedCity === c.city_ref ? " focused" : ""}`}
            data-city-ref={c.city_ref}
          >
            <input
              type="checkbox"
              name={`cities.${c.city_ref}`}
              aria-describedby={`city-note-${c.city_ref}`}
              disabled={!c.selectable}
              checked={selected.includes(c.city_ref)}
              onChange={(e) => onToggle(c.city_ref, e.target.checked)}
            />
            <span>
              <strong>
                <Zh>{c.label}</Zh>
              </strong>{" "}
              <span className={c.data_status === "real_data" ? "chip accent" : "chip warn"}>
                {c.data_status === "real_data" ? t("place.realData") : t("place.syntheticCity")}
              </span>{" "}
              {!c.selectable ? <span className="chip">{t("place.notSelectable")}</span> : null}
              <span id={`city-note-${c.city_ref}`} className="small muted" style={{ display: "block" }}>
                <Zh>{c.note}</Zh>
              </span>
            </span>
          </label>
        ))}
      </fieldset>
    </div>
  );
}
