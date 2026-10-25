// 职业目录：分类索引 + 关键词搜索 + 直接点选。目录、分组、名称、职责与搜索规则都是 E3 的（capabilities.input_catalog）。
// 搜索框里的字只用来筛选，不保存、不提交；没有结果就显示 E3 的支持范围说明，不自动换相近职业。
import { useId, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { CatalogOccupation, InputCatalog } from "../contract/types";
import { searchOccupations } from "../draft/catalog";
import { Zh } from "../i18n/biz";
import styles from "./CatalogPicker.module.css";

export function CatalogPicker({
  catalog,
  selected,
  onSelect,
  onClear,
}: {
  catalog: InputCatalog;
  selected: string | null;
  onSelect(o: CatalogOccupation): void;
  onClear(): void;
}) {
  const { t } = useTranslation();
  const uid = useId();
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState<string | null>(null);
  const [open, setOpen] = useState(selected === null);
  const groups = useMemo(() => [...catalog.groups].sort((a, b) => a.order - b.order), [catalog.groups]);
  const results = useMemo(() => searchOccupations(catalog, query, group), [catalog, query, group]);
  const current = selected ? (catalog.occupations.find((o) => o.concept_id === selected) ?? null) : null;
  const groupLabel = (id: string) => groups.find((g) => g.group_id === id)?.label ?? id;

  return (
    <div className={styles.picker} data-field="occupation">
      {current ? (
        <div className={styles.selected} data-selected-occupation={current.concept_id}>
          <p className={styles.selectedHead}>
            <strong>
              <Zh>{current.label}</Zh>
            </strong>{" "}
            <span className="chip accent">{t("facts.selectedChip")}</span>{" "}
            <span className="small muted">
              <Zh>{groupLabel(current.group_id)}</Zh>
            </span>
          </p>
          <ul className="small">
            {current.duties.map((x) => (
              <Zh as="li" key={x}>
                {x}
              </Zh>
            ))}
          </ul>
          <div className="row">
            <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
              {open ? t("facts.closePicker") : t("facts.changeOccupation")}
            </button>
            <button type="button" className="link" onClick={onClear}>
              {t("facts.clearOccupation")}
            </button>
          </div>
        </div>
      ) : selected ? (
        <p className="box warn">{t("facts.selectedUnknown", { id: selected })}</p>
      ) : null}

      {open ? (
        <div className={styles.browser}>
          <p>
            <label htmlFor={`${uid}-q`}>{t("facts.search")}</label>{" "}
            <input
              id={`${uid}-q`}
              type="search"
              autoComplete="off"
              value={query}
              placeholder={t("facts.searchPlaceholder")}
              onChange={(e) => setQuery(e.target.value)}
              className={styles.search}
              data-occupation-search
            />
            <span className="small muted"> {t("facts.searchNote")}</span>
          </p>
          <div className={styles.columns}>
            <nav className={styles.groups} aria-label={t("facts.groups")}>
              {/* 手机：下拉；桌面：分类列表（同一份选择） */}
              <select className={styles.groupSelect} aria-label={t("facts.groups")} value={group ?? ""} onChange={(e) => setGroup(e.target.value || null)}>
                <option value="">{t("facts.allGroups")}</option>
                {groups.map((g) => (
                  <option key={g.group_id} value={g.group_id}>
                    {g.label}
                  </option>
                ))}
              </select>
              <ul className={`plain ${styles.groupList}`}>
                <li>
                  <button type="button" aria-pressed={group === null} onClick={() => setGroup(null)}>
                    {t("facts.allGroups")}
                  </button>
                </li>
                {groups.map((g) => (
                  <li key={g.group_id}>
                    <button type="button" aria-pressed={group === g.group_id} onClick={() => setGroup(g.group_id)} data-group={g.group_id}>
                      <Zh>{g.label}</Zh>
                    </button>
                  </li>
                ))}
              </ul>
            </nav>
            <div className={styles.results} role="region" aria-live="polite" aria-label={t("facts.results")}>
              {results.length === 0 ? (
                <div className="box note" data-no-match>
                  <Zh as="p">{catalog.search.no_match}</Zh>
                  <button
                    type="button"
                    onClick={() => {
                      setQuery("");
                      setGroup(null);
                    }}
                  >
                    {t("facts.clearFilters")}
                  </button>
                </div>
              ) : (
                <ul className={`plain ${styles.resultList}`}>
                  {results.map((o) => {
                    const picked = o.concept_id === selected;
                    return (
                      <li key={o.concept_id} className={styles.result} data-occupation={o.concept_id}>
                        <button
                          type="button"
                          className={styles.resultButton}
                          aria-pressed={picked}
                          onClick={() => {
                            onSelect(o);
                            setOpen(false);
                          }}
                        >
                          <span className={styles.resultLabel}>
                            <Zh>{o.label}</Zh>
                            {picked ? <span className="chip accent">{t("facts.selectedChip")}</span> : null}
                          </span>
                          <span className={`small muted ${styles.resultDuties}`}>
                            {o.duties.map((x, i) => (
                              <span key={x}>
                                {i ? "；" : ""}
                                <Zh>{x}</Zh>
                              </span>
                            ))}
                          </span>
                          {group === null ? (
                            <span className={`small muted ${styles.resultGroup}`}>
                              <Zh>{groupLabel(o.group_id)}</Zh>
                            </span>
                          ) : null}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
