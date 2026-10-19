import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { ApiProvider } from "../src/api/context";
import { SampleAdapter } from "../src/api/sampleAdapter";
import { setTransport } from "../src/api/transport";
import { Allocator } from "../src/components/Allocator";
import { DraftProvider } from "../src/draft/DraftContext";

function Providers({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <ApiProvider adapter={new SampleAdapter()}>
        <DraftProvider>{children}</DraftProvider>
      </ApiProvider>
    </QueryClientProvider>
  );
}

const SIX: [string, string][] = [
  ["物质生活", "material"],
  ["安全", "safety"],
  ["发展机会", "opportunity"],
  ["休闲", "leisure"],
  ["环境", "environment"],
  ["交通便捷程度", "commute"],
];

async function renderSix(values: number[]) {
  const user = userEvent.setup();
  render(
    <Providers>
      <Allocator group="six_dimensions" title="六个方面怎样分配 100 分" help="" />
    </Providers>,
  );
  // 等能力清单读完、分配器出现（读取时的提示也是 role="status"）
  await screen.findByLabelText("物质生活");
  for (const [i, [label]] of SIX.entries()) {
    await user.type(screen.getByLabelText(label), String(values[i]));
  }
  return user;
}

beforeEach(() => {
  sessionStorage.clear();
  setTransport({ latencyMs: 0, offline: false, dropNextReceipt: false });
});

describe("分配器的＋按钮", () => {
  it("已经分满：按“交通 +5”先问从哪一项挪；选了物质，物质 30 → 25、交通 10 → 15，合计仍是 100", async () => {
    const user = await renderSix([30, 20, 20, 10, 10, 10]);
    expect(screen.getByRole("status")).toHaveTextContent("已分完");

    await user.click(screen.getByRole("button", { name: "交通便捷程度加 5 分" }));
    const dialog = await screen.findByRole("dialog", { name: /已经分满。要给“交通便捷程度”加 5 分，从哪一项挪？/ });
    // 只列出其他有分数的项，不包括交通自己
    expect(within(dialog).queryByRole("button", { name: /交通便捷程度/ })).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "物质生活（30 分）" }));

    expect(screen.getByLabelText("物质生活")).toHaveValue("25");
    expect(screen.getByLabelText("交通便捷程度")).toHaveValue("15");
    expect(screen.getByLabelText("安全")).toHaveValue("20");
    expect(screen.getByRole("status")).toHaveTextContent("已分完");
  });

  it("还剩 3 分：按“交通 +5”直接加 5 分，显示“多出 2 分”，不弹窗、不自动扣别的项", async () => {
    const user = await renderSix([30, 20, 20, 10, 10, 7]);
    expect(screen.getByRole("status")).toHaveTextContent("还剩 3 分");

    await user.click(screen.getByRole("button", { name: "交通便捷程度加 5 分" }));
    expect(screen.getByLabelText("交通便捷程度")).toHaveValue("12");
    expect(screen.getByRole("status")).toHaveTextContent("多出 2 分");
    expect(screen.queryByRole("dialog", { name: /从哪一项挪/ })).toBeNull();
    expect(screen.getByLabelText("物质生活")).toHaveValue("30");
  });

  it("已经超过 100：按＋同样先问从哪一项挪，并说明合计不再增加", async () => {
    const user = await renderSix([40, 20, 20, 10, 10, 10]);
    expect(screen.getByRole("status")).toHaveTextContent("多出 10 分");

    await user.click(screen.getByRole("button", { name: "休闲加 5 分" }));
    const dialog = await screen.findByRole("dialog", { name: /现在已经超过 100 分/ });
    await user.click(within(dialog).getByRole("button", { name: "安全（20 分）" }));
    expect(screen.getByLabelText("安全")).toHaveValue("15");
    expect(screen.getByLabelText("休闲")).toHaveValue("15");
    expect(screen.getByRole("status")).toHaveTextContent("多出 10 分");
  });
});
