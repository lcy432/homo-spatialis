import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { App } from "./App";
import { ApiProvider } from "./api/context";
import { DraftProvider } from "./draft/DraftContext";
import { FlowProvider } from "./flow/FlowContext";
import "./styles/global.css";

// 查询可以重试；提交从不自动重试（启动模拟必须走明确的提交操作和原幂等键）。
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: 2 },
    mutations: { retry: 0 },
  },
});

const root = document.getElementById("root");
if (!root) throw new Error("页面里没有 #root");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ApiProvider>
        <DraftProvider>
          <FlowProvider>
            <BrowserRouter>
              <App />
            </BrowserRouter>
          </FlowProvider>
        </DraftProvider>
      </ApiProvider>
    </QueryClientProvider>
  </StrictMode>,
);
