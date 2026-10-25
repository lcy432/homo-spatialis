import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { samplePayload } from "../src/contract/samples";
import type { ProductResult } from "../src/contract/types";
import { CityDetail, CloneDetail, ResultsOverview } from "../src/pages/results/ResultsPages";
import { ModeProvider } from "../src/ui/mode";

function renderOverview(id: string) {
  const res = samplePayload<ProductResult>(id);
  const view = render(
    <MemoryRouter>
      <ModeProvider initial="paper">
        <ResultsOverview res={res} caps={null} settings={null} base={`/dev/gallery/${id}`} />
      </ModeProvider>
    </MemoryRouter>,
  );
  return { res, ...view };
}

describe("结果第一层：公共样例的边界情况（期望值从样例本身算）", () => {
  it.each(["10", "21", "23", "84", "104"])("样例 %s：每座城的人数句子、均值与分母照公共值；C = 0 不显示均值", (id) => {
    const { res, container } = renderOverview(id);
    for (const c of res.cities ?? []) {
      const card = container.querySelector(`article[data-city="${c.city_ref}"]`) as HTMLElement;
      expect(card, c.city_ref).not.toBeNull();
      expect(
        within(card).getByText(`原计划 ${c.planned} 个分身：${c.complete} 个完成且可评价，${c.exits} 个生活出局，${c.missing} 个模拟缺失。`),
      ).toBeInTheDocument();
      if (c.mean_j) {
        expect(card.querySelector("p[data-mean]")).toHaveAttribute("data-mean", c.mean_j.exact);
        expect(within(card).getByText(`${c.mean_j.display} 是这 ${c.mean_denominator} 个完成且可评价分身的平均。`)).toBeInTheDocument();
      } else {
        expect(within(card).getByText("没有完成且评价完整的分身：没有均值（不当 0）。")).toBeInTheDocument();
      }
      if (c.missing) expect(within(card).getByText(new RegExp(`含 ${c.missing} 个模拟缺失`))).toBeInTheDocument();
    }
    expect(container.querySelectorAll('table[data-table="cities"] tbody tr')).toHaveLength(res.cities?.length ?? 0);
  });

  it("全部出局（样例 24）：没有均值、没有模拟缺失范围，不补 0", () => {
    const { res, container } = renderOverview("24");
    for (const c of res.cities ?? []) {
      expect(c.mean_j ?? null).toBeNull();
      const row = container.querySelector(`table[data-table="cities"] tr[data-city="${c.city_ref}"]`) as HTMLElement;
      expect(within(row).getByText("没有模拟缺失范围。")).toBeInTheDocument();
    }
    expect(container.querySelectorAll('[data-mean]:not([data-mean=""])').length).toBe(0);
  });

  it("评价未生成（样例 25）：不显示城市分类、均值或范围，只留可以看的事实入口", () => {
    const { container } = renderOverview("25");
    expect(screen.getByText("这次运行还没有生成评价：不显示城市分类、均值或范围。")).toBeInTheDocument();
    expect(container.querySelector('table[data-table="cities"]')).toBeNull();
    expect(container.querySelectorAll("article[data-city]").length).toBe(0);
  });
});

describe("结果第二、三层", () => {
  it("单城详情：J、L、V 并排解释；读不到这次运行绑定的运行前检查时如实写缺失", () => {
    const res = samplePayload<ProductResult>("104");
    const city = res.cities?.[0]?.city_ref as string;
    render(
      <MemoryRouter>
        <ModeProvider initial="paper">
          <CityDetail res={res} caps={null} cityRef={city} boundPreflight={null} base="/x" />
        </ModeProvider>
      </MemoryRouter>,
    );
    expect(screen.getByText(/L：实际生活评价/)).toBeInTheDocument();
    expect(screen.getByText(/V：期末估值/)).toBeInTheDocument();
    expect(screen.getByText(/不是 L 加 V 等于 J/)).toBeInTheDocument();
    expect(screen.getByText(/这次运行绑定的运行前检查读不到/)).toBeInTheDocument();
  });

  it("分身记录：每一年一张卡；模型的计划理由单独一栏说明本版没有公开", () => {
    const res = samplePayload<ProductResult>("10");
    const clone = res.clones?.[0];
    const { container } = render(
      <MemoryRouter>
        <ModeProvider initial="paper">
          <CloneDetail res={res} cloneRef={clone?.clone_ref as string} base="/x" />
        </ModeProvider>
      </MemoryRouter>,
    );
    expect(container.querySelectorAll("article[data-year]")).toHaveLength(clone?.years?.length ?? 0);
    expect(screen.getByText(/本版结果没有公开分身自己的计划理由/)).toBeInTheDocument();
  });
});
