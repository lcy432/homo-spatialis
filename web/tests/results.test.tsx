import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { ProgressView } from "../src/components/Progress";
import { ResultView } from "../src/components/Result";
import { CloneStoryView } from "../src/components/Story";
import { samplePayload } from "../src/contract/samples";
import type { ProductCapabilities, ProductResult, RunProgress } from "../src/contract/types";
import { assertMessage } from "../src/contract/validate";
import { HEALTH_MONTH, pendingLabel } from "../src/contract/pending-labels";

const caps = samplePayload<ProductCapabilities>("01");

function renderResult(id: string) {
  const result = samplePayload<ProductResult>(id);
  return render(
    <MemoryRouter>
      <ResultView result={result} />
    </MemoryRouter>,
  );
}

function cityCard(container: HTMLElement, ref: string): HTMLElement {
  const el = container.querySelector(`[data-city="${ref}"]`);
  if (!el) throw new Error(`没有城市卡 ${ref}`);
  return el as HTMLElement;
}

describe("城市结果：分母、空值与类别", () => {
  it("正常样例：均值、分母句子、范围与比较原文", () => {
    const { container } = renderResult("10");
    const k51 = within(cityCard(container, "US:SYNTH2026:K51"));
    expect(k51.getByText("1 个完成且可评价分身的平均 J")).toBeInTheDocument();
    expect(k51.getAllByText("0.6078").length).toBeGreaterThan(0);
    expect(k51.getByText("分母是完成且可评价的分身，不是原计划的 1 个。")).toBeInTheDocument();
    expect(screen.getByText(/不是显著性检验/)).toBeInTheDocument();
    expect(screen.getByText(/A：合成城 K51\s+B：合成城 M52/)).toBeInTheDocument();
  });

  it("C = 0：不显示 0，写“没有可用于平均的完整结果”；范围 0–1、分母 1（出局者不进）", () => {
    const { container } = renderResult("23");
    const m52 = within(cityCard(container, "US:SYNTH2026:M52"));
    expect(m52.getByText("没有可用于平均的完整结果")).toBeInTheDocument();
    expect(m52.queryByText("1 个完成且可评价分身的平均 J")).toBeNull();
    expect(m52.getByText(/分母 1 = 完成 \+ 模拟问题，出局者不进/)).toBeInTheDocument();
    expect(m52.getByText(/原计划 2 个分身：0 个完成且可评价，1 个生活出局，1 个模拟问题/)).toBeInTheDocument();
    // 模拟问题写成模拟问题，不写成生活出局
    expect(screen.getAllByText(/不写成生活出局/).length).toBeGreaterThan(0);
  });

  it("全部出局：均值与范围都为空", () => {
    const { container } = renderResult("24");
    for (const ref of ["US:SYNTH2026:K51", "US:SYNTH2026:M52"]) {
      const c = within(cityCard(container, ref));
      expect(c.getByText("没有可用于平均的完整结果")).toBeInTheDocument();
      expect(c.getByText("没有范围")).toBeInTheDocument();
    }
  });

  it("还没有生成评价：不显示任何城市分类、均值或范围", () => {
    const { container } = renderResult("25");
    expect(screen.getByText("这次运行还没有生成评价：不显示任何城市分类、均值或范围。")).toBeInTheDocument();
    expect(container.querySelector("[data-city]")).toBeNull();
    expect(screen.queryByText(/原计划/)).toBeNull();
  });

  it("程序错误暂停的结果：两个分身都是模拟问题", () => {
    renderResult("27b");
    expect(screen.getAllByText(/不是生活出局/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/生活不下去/)).toBeNull();
  });

  it("分身故事：事件链标为环境记录，模型理由只是占位，健康月份用临时文字", () => {
    const result = samplePayload<ProductResult>("23");
    const clone = result.clones!.find((c) => c.clone_ref === "u503.k51.r1")!;
    render(
      <MemoryRouter>
        <CloneStoryView clone={clone} result={result} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "环境记录" })).toBeInTheDocument();
    expect(screen.getByText(/月初新发大病/)).toBeInTheDocument();
    expect(screen.getByText(/本版结果还没有提供分身自己的理由/)).toBeInTheDocument();
    expect(screen.getAllByText(/大病/).length).toBeGreaterThan(0);
  });
});

describe("进度", () => {
  function progressOf(patch: Partial<RunProgress>): RunProgress {
    const base = samplePayload<RunProgress>("09");
    // 构造的进度也必须符合共享结构（排队、运行中两种状态没有 E3 样例，需求 A7）
    return assertMessage<RunProgress>("RunProgress", { ...base, ...patch });
  }

  it("排队与运行中：只说现在的阶段，不写成全部完成", () => {
    const { rerender } = render(
      <MemoryRouter>
        <ProgressView progress={progressOf({ stage: "queued", stage_zh: "排队中", report_available: false, clones: [] })} caps={caps} years={3} />
      </MemoryRouter>,
    );
    expect(screen.getByText(/排队中：还没有开始/)).toBeInTheDocument();
    expect(screen.queryByText("查看结果")).toBeNull();
    const running = progressOf({
      stage: "running",
      stage_zh: "运行中",
      report_available: false,
      resumable: true,
      clones: [
        { clone_ref: "u501.k51.r0", city_ref: "US:SYNTH2026:K51", status: "running", status_zh: "进行中", years_committed: 2 },
      ],
    });
    rerender(
      <MemoryRouter>
        <ProgressView progress={running} caps={caps} years={3} />
      </MemoryRouter>,
    );
    expect(screen.getByText(/正在执行/)).toBeInTheDocument();
    expect(screen.getByText("已提交 2 / 3 年")).toBeInTheDocument();
    expect(screen.getByText(/由维护者处理/)).toBeInTheDocument();
  });

  it("服务失败停下（契约示例 33）：写明不是生活结果", () => {
    render(
      <MemoryRouter>
        <ProgressView progress={samplePayload<RunProgress>("33")} caps={caps} years={null} />
      </MemoryRouter>,
    );
    expect(screen.getByText(/模型服务暂时不可用/)).toBeInTheDocument();
    expect(screen.getByText(/不是生活结果/)).toBeInTheDocument();
  });
});

describe("待 E3 提供中文的临时文字", () => {
  it("登记过的键给出转写与来源；没登记的如实显示代码", () => {
    expect(pendingLabel(HEALTH_MONTH, "major")).toMatchObject({ text: "大病", pending: true });
    expect(pendingLabel(HEALTH_MONTH, "terminal")).toMatchObject({ text: "未登记的状态（代码：terminal）", known: false });
  });
});
