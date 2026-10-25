import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ReactNode, useReducer } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { Allocator } from "../src/components/Allocator";
import { PreflightView } from "../src/components/RunViews";
import { CemBar, CompareChart } from "../src/components/results/CompareChart";
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
