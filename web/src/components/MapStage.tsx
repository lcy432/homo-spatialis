// 地图页的版面。稿纸：标题、地图（视觉重心，占满宽度）、然后是列表与说明，照普通文档往下读。
// 地球：地球铺满整块舞台，其余内容（标题、列表、说明、下一步）收进右侧可以收起 / 展开的面板；窄屏时面板从底部升起。
// 收起只是隐藏（inert + aria-hidden），不卸载内容：填到一半的东西、焦点能回到的地方都还在。
import { type ReactNode, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMode } from "../ui/mode";
import styles from "./MapStage.module.css";

export function MapStage({
  head,
  map,
  children,
  panelLabel,
}: {
  /** 标题与说明：稿纸里在地图之前，地球里在面板顶部。 */
  head: ReactNode;
  map: ReactNode;
  children: ReactNode;
  panelLabel: string;
}) {
  const { mode } = useMode();
  const { t } = useTranslation();
  const [open, setOpen] = useState(true);
  const id = useId();
  if (mode !== "globe") {
    return (
      <>
        {head}
        <div className={styles.paperMap}>{map}</div>
        {children}
      </>
    );
  }
  return (
    <div className={styles.stage} data-stage="globe" data-panel={open ? "open" : "closed"}>
      <div className={styles.globe}>{map}</div>
      <button
        type="button"
        className={`${styles.panelToggle} ${open ? "" : styles.panelToggleClosed}`}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
      >
        {open ? t("maps.panelHide") : t("maps.panelShow")}
      </button>
      <aside id={id} className={styles.panel} aria-label={panelLabel} aria-hidden={!open} inert={!open}>
        <div className={styles.panelInner}>
          {head}
          {children}
        </div>
      </aside>
    </div>
  );
}
