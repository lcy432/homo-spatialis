import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ReactNode, useReducer } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { Allocator } from "../src/components/Allocator";
import { PreflightView } from "../src/components/RunViews";
import { CemBar, CompareChart } from "../src/components/results/CompareChart";
import { monthStates } from "../src/components/results/MonthStrip";
import { YearReport } from "../src/components/results/YearReport";
import { samplePayload } from "../src/contract/samples";
import type { CityResult, DraftView, ProductCapabilities, ProductPreflight, ProductResult, YearFacts } from "../src/contract/types";
import { DraftEditorContext, type DraftEditorValue } from "../src/draft/DraftEditor";
import { draftReducer, initialDraftState } from "../src/draft/reducer";
import { i18n } from "../src/i18n/index";
import { ModeProvider } from "../src/ui/mode";
import { colorWindow } from "../src/view/colorScale";
import { type CityRow, cityRows } from "../src/view/results";

const caps = samplePayload<ProductCapabilities>("01");
const created = samplePayload<DraftView>("62");

/** 不起服务的编辑器：只有状态更新函数（保存状态固定为已保存）。 */
function EditorHarness({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(draftReducer, created.draft, initialDraftState);
  const value: DraftEditorValue = {
    caps,
    draftId: created.draft.draft_id,
    base: created,
    state,
    dispatch,
    save: { kind: "saved", revision: created.draft.revision },
    flush: async () => ({ kind: "saved", revision: created.draft.revision }),
    retrySave: () => undefined,
    useServerVersion: () => undefined,
    keepMyVersion: () => undefined,
  };
  return (
    <ModeProvider initial="paper">
      <DraftEditorContext.Provider value={value}>{children}</DraftEditorContext.Provider>
    </ModeProvider>
  );
}

const exact = (v: number) => ({ exact: String(v), display: v.toFixed(4) });

describe("城市比较图", () => {
  it("合法的展示夹具：完整者均值 0.3 落在模拟缺失范围 [0.45, 0.55] 外——圆点与横条各按公共值定位，范围不被撑大", () => {
    const city = {
      city_ref: "X:TEST:1",
      label: "展示夹具城",
      planned: 2,
      complete: 1,
      exits: 0,
      missing: 1,
      mean_denominator: 1,
      range_denominator: 2,
      mean_j: exact(0.3),
      range_lower: exact(0.45),
      range_upper: exact(0.55),
    } as unknown as CityResult;
    const rows: CityRow[] = [{ cityRef: city.city_ref, label: city.label, pointer: "/cities/0", labelPointer: "/cities/0/label", country: null, city, mean: 0.3, low: 0.45, high: 0.55 }];
    const { container } = render(
      <ModeProvider initial="paper">
        <CompareChart rows={rows} window={colorWindow([0.3])} />
      </ModeProvider>,
    );
    const dot = container.querySelector<HTMLElement>("[data-mean]");
    const band = container.querySelector<HTMLElement>("[data-band]");
    const left = (el: HTMLElement | null) => Number.parseFloat(el?.style.left ?? "NaN");
    expect(left(dot)).toBeLessThan(left(band));
    expect(left(band) + Number.parseFloat(band?.style.width ?? "0")).toBeLessThanOrEqual(100.0001);
    expect(band?.dataset.band).toBe("0.45..0.55");
  });

  it("零宽范围（上下界都是 0.5）：画在 0.5 的位置上，是一道竖线，不向右伪造宽度（独立核查 F4）", () => {
    const city = {
      city_ref: "X:TEST:2",
      label: "零宽范围城",
      planned: 1,
      complete: 1,
      exits: 0,
      missing: 0,
      mean_denominator: 1,
      range_denominator: 1,
      mean_j: exact(0.5),
      range_lower: exact(0.5),
      range_upper: exact(0.5),
    } as unknown as CityResult;
    const wide = { ...city, city_ref: "X:TEST:3", label: "普通范围城", mean_j: exact(0.52), range_lower: exact(0.5), range_upper: exact(0.56) } as unknown as CityResult;
    const rows: CityRow[] = [
      { cityRef: city.city_ref, label: city.label, pointer: "/cities/0", labelPointer: "/cities/0/label", country: null, city, mean: 0.5, low: 0.5, high: 0.5 },
      { cityRef: wide.city_ref, label: wide.label, pointer: "/cities/1", labelPointer: "/cities/1/label", country: null, city: wide, mean: 0.52, low: 0.5, high: 0.56 },
    ];
    const { container } = render(
      <ModeProvider initial="paper">
        <CompareChart rows={rows} window={colorWindow([0.5, 0.52])} />
      </ModeProvider>,
    );
    const [point, band] = [...container.querySelectorAll<HTMLElement>("[data-band]")] as [HTMLElement, HTMLElement];
    const dot = container.querySelector<HTMLElement>('[data-city="X:TEST:2"] [data-mean]');
    expect(point.dataset.band).toBe("0.5..0.5");
    expect(point.style.width).toBe(""); // 没有宽度：不是一段
    expect(point.style.left).toBe(dot?.style.left); // 与均值 0.5 在同一个位置
    // 普通范围：左端在 0.5，宽度正好是 0.5→0.56 的距离（与零宽范围的左端相同）
    expect(band.style.left).toBe(point.style.left);
    expect(Number.parseFloat(band.style.width)).toBeGreaterThan(0);
  });

  it("C = 0 的城市没有圆点、不上最低分的颜色；含 M 但有均值的城市照样有均值颜色", () => {
    const res = samplePayload<ProductResult>("23");
    const rows = cityRows(res, null);
    const w = colorWindow(rows.map((r) => r.mean).filter((v): v is number => v !== null));
    const { container } = render(
      <ModeProvider initial="paper">
        <CompareChart rows={rows} window={w} />
      </ModeProvider>,
    );
    for (const r of rows) {
      const row = container.querySelector(`[data-city="${r.cityRef}"]`) as HTMLElement;
      if (r.city.complete === 0) {
        expect(row.querySelector("[data-mean]")).toBeNull();
        expect(within(row).getByText("没有可用于着色的均值")).toBeInTheDocument();
      } else {
        expect(row.querySelector<HTMLElement>("[data-mean]")?.style.background).not.toBe("");
      }
    }
  });

  it("C / E / M 组成条：三段都有文字，不只靠颜色", () => {
    render(<CemBar planned={10} c={7} e={2} m={1} />);
    expect(screen.getByRole("img", { name: /原计划 10 个分身：7 个完成且可评价，2 个生活出局，1 个模拟缺失/ })).toBeInTheDocument();
    expect(screen.getByText("C 7")).toBeInTheDocument();
    expect(screen.getByText("E 2")).toBeInTheDocument();
    expect(screen.getByText("M 1")).toBeInTheDocument();
  });
});

describe("分身的年度生活记录", () => {
  const base = samplePayload<ProductResult>("104").clones?.[0]?.years?.[0] as YearFacts;

  it("消费篮子的 “0” 与 “—”（那个月没有生活）分开显示", () => {
    const year: YearFacts = {
      ...base,
      months_lived: 10,
      consumption: { baskets_by_month: ["1", "1", "0", "1", "1", "1", "1", "1", "1", "1", "—", "—"], opportunity_ids: ["x"] },
    };
    render(<YearReport year={year} pointer="/clones/0/years/0" />);
    const strip = screen.getByRole("list", { name: /个人消费篮子/ });
    const cells = within(strip).getAllByRole("listitem");
    expect(cells[2]).toHaveAttribute("title", "3 · 0");
    expect(cells[10]).toHaveAttribute("title", "11 · —");
  });

  it("服务月份：全年都生活、只在 7–12 月安排清洁，7 月安排了没获得——1–6 月是“没有安排”，不是“没有生活”（独立核查 F3）", () => {
    const year: YearFacts = {
      ...base,
      months_lived: 12,
      services: [
        {
          opportunity_id: "svc.clean",
          label: "上门清洁",
          months_planned: [7, 8, 9, 10, 11, 12],
          months_covered: [8, 9, 10, 11, 12],
          months_not_obtained: [7],
        },
      ],
    };
    render(<YearReport year={year} pointer="/clones/0/years/0" />);
    const strip = screen.getByRole("list", { name: /上门清洁/ });
    const cells = within(strip).getAllByRole("listitem");
    expect(cells[0]).toHaveAttribute("title", "1 · 没有安排");
    expect(cells[5]).toHaveAttribute("title", "6 · 没有安排");
    expect(cells[6]).toHaveAttribute("title", "7 · 已安排、没获得");
    expect(cells[7]).toHaveAttribute("title", "8 · 已获得");
    expect(cells[11]).toHaveAttribute("title", "12 · 已获得");
    expect(within(strip).queryByTitle(/没有生活/)).toBeNull();
  });

  it("服务月份：年中（第 8 个月）结束生活——9–12 月是“没有生活”；设备条带说“在用 / 不在用”，不说安排", () => {
    const year: YearFacts = {
      ...base,
      months_lived: 8,
      services: [{ opportunity_id: "svc.net", label: "测试用联网服务", months_planned: [1, 2, 3, 4, 5, 6, 7, 8], months_covered: [1, 2, 3, 4, 5, 6, 7, 8], months_not_obtained: [] }],
      devices: [{ opportunity_id: "dev.washer", label: "测试用洗衣机", months_in_service: [3, 4, 5, 6, 7, 8] }],
    };
    render(<YearReport year={year} pointer="/clones/0/years/0" />);
    const svc = within(screen.getByRole("list", { name: /测试用联网服务/ })).getAllByRole("listitem");
    expect(svc[7]).toHaveAttribute("title", "8 · 已获得");
    expect(svc[8]).toHaveAttribute("title", "9 · 没有生活");
    const dev = within(screen.getByRole("list", { name: /测试用洗衣机/ })).getAllByRole("listitem");
    expect(dev[0]).toHaveAttribute("title", "1 · 不在用");
    expect(dev[2]).toHaveAttribute("title", "3 · 在用");
    expect(dev[8]).toHaveAttribute("title", "9 · 没有生活");
  });

  it("月份状态的纯函数：公开资料里既没有获得也没有安排列表时是“记录不足”，不补零", () => {
    expect(monthStates(null, 12, null)).toEqual(Array(12).fill("unknown"));
    expect(monthStates([2], 3, undefined)).toEqual(["off", "on", "off", ...Array(9).fill("none")]);
  });

  it("片区居民的典型通勤与这个分身的实际通勤分开写；年度已支付总额写明不是月度流水", () => {
    render(<YearReport year={base} pointer="/clones/0/years/0" />);
    expect(screen.getByText(/片区的典型值，不是你的实际通勤|片区典型通勤：未知/)).toBeInTheDocument();
    expect(screen.getByText("实际通勤（逐月执行记录）")).toBeInTheDocument();
    expect(screen.getByText("这一年已支付的总额（年度合计，不是月度流水）")).toBeInTheDocument();
  });
});

describe("独立评分控件（饼图 + 滑块）", () => {
  const slider = (key: string) => document.querySelector<HTMLInputElement>(`[data-slider="${key}"]`) as HTMLInputElement;
  const shown = (key: string) => document.querySelector<HTMLElement>(`[data-key="${key}"] [data-value]`)?.dataset.value;
  const weight = (key: string) => document.querySelector<HTMLElement>(`[data-key="${key}"] [data-value]`)?.dataset.weight;
  /** 一次指针手势：按下 → 输入 → 松开。 */
  const drag = (key: string, to: number) => {
    const el = slider(key);
    fireEvent.pointerDown(el);
    fireEvent.input(el, { target: { value: String(to) } });
    fireEvent.pointerUp(el);
  };

  it("初始不预填：六项都是“未答”，没有任何扇区，状态写“还没有评分”；滑块停在 0 不等于回答 0", () => {
    render(
      <EditorHarness>
        <Allocator group="six_dimensions" title="六个方面" help="" />
      </EditorHarness>,
    );
    expect(document.querySelectorAll('[data-allocator="six_dimensions"] [data-state="unanswered"]')).toHaveLength(6);
    expect(document.querySelectorAll("[data-slice]")).toHaveLength(0);
    expect(document.querySelector('[data-allocator="six_dimensions"]')).toHaveAttribute("data-preview", "none");
    expect(screen.getByRole("status")).toHaveTextContent("还没有评分");
    expect(slider("safety").value).toBe("0");
  });

  it("80/40/40/20/10/10 → 临时预览 40/20/20/10/5/5（精确），原始合计 200；改一项不改别的项的原始评分", () => {
    render(
      <EditorHarness>
        <Allocator group="six_dimensions" title="六个方面" help="" />
      </EditorHarness>,
    );
    const vals: Record<string, number> = { material: 80, safety: 40, opportunity: 40, leisure: 20, environment: 10, commute: 10 };
    for (const [k, v] of Object.entries(vals)) drag(k, v);
    expect(document.querySelector('[data-allocator="six_dimensions"]')).toHaveAttribute("data-preview", "complete");
    expect(document.querySelector('[data-allocator="six_dimensions"]')).toHaveAttribute("data-rating-total", "200");
    expect(weight("material")).toBe("40");
    expect(weight("safety")).toBe("20");
    expect(weight("environment")).toBe("5");
    expect(document.querySelectorAll("[data-slice]")).toHaveLength(6);
    drag("safety", 60);
    expect(shown("safety")).toBe("60");
    for (const [k, v] of Object.entries(vals)) if (k !== "safety") expect(shown(k)).toBe(String(v));
  });

  it("两次独立手势是两条撤销记录（E5-R5）；拖到一半只是预览，松手才写入", async () => {
    const user = userEvent.setup();
    render(
      <EditorHarness>
        <Allocator group="six_dimensions" title="六个方面" help="" />
      </EditorHarness>,
    );
    drag("safety", 20);
    drag("safety", 40);
    expect(shown("safety")).toBe("40");
    await user.click(screen.getByRole("button", { name: "撤销" }));
    expect(shown("safety")).toBe("20");
    await user.click(screen.getByRole("button", { name: "撤销" }));
    expect(document.querySelector('[data-key="safety"]')).toHaveAttribute("data-state", "unanswered");
    await user.click(screen.getByRole("button", { name: "重做" }));
    expect(shown("safety")).toBe("20");
    // 中途的输入只改预览，不写草稿
    const el = slider("leisure");
    fireEvent.pointerDown(el);
    fireEvent.input(el, { target: { value: "33" } });
    expect(document.querySelector('[data-key="leisure"]')).toHaveAttribute("data-state", "unanswered");
    fireEvent.pointerUp(el);
    expect(shown("leisure")).toBe("33");
  });

  it("部分已填：预览标为临时并列出未答项；明确 0（“记为 0”）与未答分开；全零时没有可用比例", async () => {
    const user = userEvent.setup();
    render(
      <EditorHarness>
        <Allocator group="six_dimensions" title="六个方面" help="" />
      </EditorHarness>,
    );
    drag("safety", 20);
    expect(document.querySelector('[data-allocator="six_dimensions"]')).toHaveAttribute("data-preview", "partial");
    expect(screen.getByRole("status")).toHaveTextContent("临时预览（已填 1/6 项）");
    expect(screen.getByText(/未答：/)).toHaveTextContent("物质生活");
    expect(document.querySelectorAll('[data-state="unanswered"]')).toHaveLength(5);
    // “记为 0”：明确的 0
    await user.click(within(document.querySelector('[data-key="leisure"]') as HTMLElement).getByRole("button", { name: "记为 0" }));
    expect(shown("leisure")).toBe("0");
    expect(document.querySelector('[data-key="leisure"]')).toHaveAttribute("data-state", "provided");
    // 全零
    drag("safety", 0);
    expect(document.querySelector('[data-allocator="six_dimensions"]')).toHaveAttribute("data-preview", "zero");
    expect(screen.getByRole("status")).toHaveTextContent("至少有一项大于 0");
  });

  it("同等重视：每项 50、预览每项 50/3；数字框写 120 或 50/3 原样保留并提示，不截断", async () => {
    const user = userEvent.setup();
    render(
      <EditorHarness>
        <Allocator group="six_dimensions" title="六个方面" help="" />
      </EditorHarness>,
    );
    await user.click(screen.getByRole("button", { name: "同等重视" }));
    for (const k of ["material", "safety", "opportunity", "leisure", "environment", "commute"]) {
      expect(shown(k)).toBe("50");
      expect(weight(k)).toBe("50/3");
    }
    const input = document.querySelector<HTMLInputElement>('input[name="six_dimensions.safety"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: "120" } });
    expect(input.value).toBe("120");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText(/每项评分在 0 到 100 之间/)).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "50/3" } });
    expect(screen.getByText(/评分写十进制数/)).toBeInTheDocument();
  });

  it("切换到英文：原始评分不变（只换界面文字）", async () => {
    render(
      <EditorHarness>
        <Allocator group="six_dimensions" title="six" help="" />
      </EditorHarness>,
    );
    drag("safety", 30);
    await i18n.changeLanguage("en");
    expect(await screen.findByRole("button", { name: "Equal weight" })).toBeInTheDocument();
    expect(shown("safety")).toBe("30");
    expect(screen.getByRole("status")).toHaveTextContent("Temporary preview (1/6 items filled)");
  });
});

describe("运行前检查", () => {
  it("范围说明的主题用界面的名称，不露出内部代码；检查里没写停机上限时指回这次保存的设置", () => {
    const p = samplePayload<ProductPreflight>("51");
    expect(p.planner.max_cost ?? null).toBeNull();
    const { container } = render(
      <MemoryRouter>
        <ModeProvider initial="paper">
          <PreflightView preflight={p} caps={null} />
        </ModeProvider>
      </MemoryRouter>,
    );
    const summaries = [...container.querySelectorAll("details[data-topic] > summary")].map((x) => x.textContent);
    // 样例 51 六个主题都有
    expect([...summaries].sort()).toEqual(["职业", "税", "通勤", "资料", "年度菜单", "本版未支持的内容"].sort());
    for (const code of ["occupations", "tax", "commute", "menus", "unsupported", "data"]) expect(summaries).not.toContain(code);
    expect(screen.getByText(/这份运行前检查里没有写/)).toBeInTheDocument();
  });
});
