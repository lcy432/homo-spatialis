// 多选面板（证书、休闲兴趣、口味与菜系）：点一项就选中，再点取消；已选的集中显示、可移除。
// 条目少直接摆出来；条目多（10 个及以上）给 E3 定义的分组索引与搜索——搜索只是过滤，不增加选项、不改答案；
// 命中 E3 的搜索说明（例如“川菜”→ 中餐、本版不区分地方菜）就把说明显示出来。
import { type ReactNode, useId, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { SearchNote } from "../contract/types";
import { termsMatch, queryWords } from "../draft/catalog";
import { useTextOf } from "../display/names";
import { Zh } from "../i18n/biz";
import styles from "./ChoicePanel.module.css";

export interface PanelItem {
  key: string;
  label: string;
  /** 名称在能力清单里的位置（显示作用域按它取译文）。 */
  pointer: string;
  groupId: string;
  searchTerms: readonly string[];
  /** 条目下的一行小字（例如证书对应的职业），已经是当前语言的文字。 */
  note?: ReactNode;
}

export interface PanelGroupDef {
  id: string;
  label: string;
  /** 分组名称的位置；界面自己的分组（例如证书的唯一分组）用 null，直接显示 label。 */
  pointer: string | null;
}

const SEARCH_THRESHOLD = 10;

export function ChoicePanel({
  name,
  items,
  groups,
  selected,
  onToggle,
  notes,
  disabled,
}: {
  name: string;
  items: readonly PanelItem[];
  groups: readonly PanelGroupDef[];
  selected: readonly string[];
  onToggle(key: string, selected: boolean): void;
  /** E3 的搜索说明，附每条说明文字的位置。 */
  notes?: readonly (SearchNote & { pointer: string })[];
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const uid = useId();
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState<string | null>(null);
  const searchable = items.length >= SEARCH_THRESHOLD;
  const shown = useMemo(
    () => items.filter((it) => (group === null || it.groupId === group) && (searchable ? termsMatch(query, it.searchTerms) : true)),
    [items, group, query, searchable],
  );
  const hitNotes = useMemo(
    () => (searchable && queryWords(query).length ? (notes ?? []).filter((n) => termsMatch(query, n.terms)) : []),
    [notes, query, searchable],
  );
  const text = useTextOf();
  const itemOf = (k: string) => items.find((i) => i.key === k);
  /** 当前语言下的条目名称（给 aria-label 这类纯文本用）。 */
  const labelText = (k: string) => {
    const it = itemOf(k);
    return it ? text(it.pointer, it.label) : k;
  };
  const groupText = (g: PanelGroupDef) => (g.pointer ? text(g.pointer, g.label) : g.label);
  const byGroup = groups.filter((g) => shown.some((it) => it.groupId === g.id));

  return (
    <div className={styles.panel} data-panel={name} data-selected-count={selected.length}>
      <p className={styles.summary} role="status" aria-live="polite">
        <span className={styles.count}>{t("panel.selectedCount", { n: selected.length })}</span>
        {selected.length ? (
          <span className={styles.chips}>
            {selected.map((k) => (
              <span key={k} className={styles.chip} data-selected-key={k}>
                <Zh pointer={itemOf(k)?.pointer ?? "/__unknown_item__"}>{labelText(k) === k ? k : itemOf(k)?.label}</Zh>
                <button type="button" className={styles.remove} aria-label={t("panel.remove", { label: labelText(k) })} onClick={() => onToggle(k, false)} disabled={disabled}>
                  ×
                </button>
              </span>
            ))}
          </span>
        ) : null}
      </p>

      {searchable ? (
        <div className={styles.tools}>
          <label htmlFor={`${uid}-q`} className="small">
            {t("panel.search")}
          </label>{" "}
          <input id={`${uid}-q`} type="search" autoComplete="off" value={query} onChange={(e) => setQuery(e.target.value)} className={styles.search} data-panel-search />
          <select aria-label={t("panel.groups")} value={group ?? ""} onChange={(e) => setGroup(e.target.value || null)}>
            <option value="">{t("panel.allGroups")}</option>
            {groups.map((g) => (
              <option key={g.id} value={g.id}>
                {groupText(g)}
              </option>
            ))}
          </select>
          <span className="small muted">{t("panel.searchNote")}</span>
        </div>
      ) : null}

      {hitNotes.map((n) => (
        <p key={n.note_id} className="box note small" data-search-note={n.note_id}>
          <Zh pointer={n.pointer}>{n.note}</Zh>
        </p>
      ))}

      {shown.length === 0 ? (
        <p className="small muted" data-panel-empty>
          {t("panel.noMatch")}{" "}
          <button
            type="button"
            className="link"
            onClick={() => {
              setQuery("");
              setGroup(null);
            }}
          >
            {t("facts.clearFilters")}
          </button>
        </p>
      ) : (
        byGroup.map((g) => (
          <fieldset key={g.id} className={styles.group}>
            <legend>
              {g.pointer ? <Zh pointer={g.pointer}>{g.label}</Zh> : g.label}
            </legend>
            <div className={styles.items}>
              {shown
                .filter((it) => it.groupId === g.id)
                .map((it) => {
                  const on = selected.includes(it.key);
                  return (
                    <label key={it.key} className={`choice ${styles.item} ${on ? styles.itemOn : ""}`} data-panel-item={it.key}>
                      <input type="checkbox" name={`${name}.${it.key}`} checked={on} disabled={disabled} onChange={(e) => onToggle(it.key, e.target.checked)} />
                      <span>
                        <Zh pointer={it.pointer}>{it.label}</Zh>
                        {it.note ? (
                          <span className="small muted" style={{ display: "block" }}>
                            {it.note}
                          </span>
                        ) : null}
                      </span>
                    </label>
                  );
                })}
            </div>
          </fieldset>
        ))
      )}
    </div>
  );
}
