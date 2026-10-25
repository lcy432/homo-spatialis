import { render, screen, within } from "@testing-library/react";
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
    const rows: CityRow[] = [{ cityRef: city.city_ref, label: city.label, country: null, city, mean: 0.3, low: 0.45, high: 0.55 }];
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
      { cityRef: city.city_ref, label: city.label, country: null, city, mean: 0.5, low: 0.5, high: 0.5 },
      { cityRef: wide.city_ref, label: wide.label, country: null, city: wide, mean: 0.52, low: 0.5, high: 0.56 },
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
    render(<YearReport year={year} />);
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
    render(<YearReport year={year} />);
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
    render(<YearReport year={year} />);
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
    render(<YearReport year={base} />);
    expect(screen.getByText(/片区的典型值，不是你的实际通勤|片区典型通勤：未知/)).toBeInTheDocument();
    expect(screen.getByText("实际通勤（逐月执行记录）")).toBeInTheDocument();
    expect(screen.getByText("这一年已支付的总额（年度合计，不是月度流水）")).toBeInTheDocument();
  });
});

describe("分配器", () => {
  it("均分（精确 50/3、显示 16.7）→ 安全改成 20 先问改手动 → 精确多出 10/3 → 用户亲手从物质减 → 已分完", async () => {
    const user = userEvent.setup();
    render(
      <EditorHarness>
        <Allocator group="six_dimensions" title="六维" help="" />
      </EditorHarness>,
    );
    await user.click(screen.getByRole("button", { name: "平均分配" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "平均分配" }));
    expect(screen.getByText(/你选了平均分配：每项 16.7 分（精确值 50\/3）/)).toBeInTheDocument();

    const safety = screen.getByLabelText("安全");
    await user.clear(safety);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "改成手动分配" }));
    await user.clear(screen.getByLabelText("安全"));
    await user.type(screen.getByLabelText("安全"), "20");
    expect(screen.getByRole("status")).toHaveTextContent("多出 3.3 分");
    expect(screen.getByRole("status")).toHaveTextContent("10/3");

    await user.click(screen.getByRole("button", { name: /从哪一项减/ }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: /物质生活/ }));
    expect(screen.getByRole("status")).toHaveTextContent("已分完");
    expect((screen.getByLabelText("物质生活") as HTMLInputElement).value).toBe("40/3");
  });

  it("切换到英文：精确值与已填的数不变（只换界面文字）", async () => {
    const user = userEvent.setup();
    render(
      <EditorHarness>
        <Allocator group="six_dimensions" title="six" help="" />
      </EditorHarness>,
    );
    await user.type(screen.getByLabelText("安全"), "30");
    await i18n.changeLanguage("en");
    expect(await screen.findByRole("button", { name: "Split equally" })).toBeInTheDocument();
    expect((screen.getByLabelText("安全") as HTMLInputElement).value).toBe("30");
    expect(screen.getByRole("status")).toHaveTextContent("Points left: 70");
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
