// 固定的英文字体（只带 latin 子集，随包打包，不走网络）。稿纸：Great Vibes（大标题）+ EB Garamond；地球：Rajdhani（大标题）+ Space Grotesk（中标题）+ IBM Plex Sans / Mono。
import "@fontsource/great-vibes/latin-400.css";
import "@fontsource/eb-garamond/latin-400.css";
import "@fontsource/eb-garamond/latin-400-italic.css";
import "@fontsource/eb-garamond/latin-600.css";
import "@fontsource/rajdhani/latin-600.css";
import "@fontsource/space-grotesk/latin-500.css";
import "@fontsource/space-grotesk/latin-600.css";
import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-400-italic.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { App } from "./App";
import { SessionProvider, captureInviteFromLocation } from "./api/session";
import { setupI18n } from "./i18n/index";
import { dropLegacyTabStorage } from "./prefs";
import { ModeProvider } from "./ui/mode";
import "./styles/global.css";

// 专属链接的秘密：在任何渲染之前读进内存，立刻从地址栏与历史里抹掉。
captureInviteFromLocation();
// E5-A 离线原型的标签页存储（v2 样例答案）不并入服务身份。
dropLegacyTabStorage();
setupI18n();

// 查询的重试由各查询自己决定（断线退避、429 按服务的等待秒数）；修改从不自动重试：提交、恢复、取消、删除都走明确的操作。
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: false },
    mutations: { retry: 0 },
  },
});

const root = document.getElementById("root");
if (!root) throw new Error("页面里没有 #root");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <SessionProvider>
        <ModeProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </ModeProvider>
      </SessionProvider>
    </QueryClientProvider>
  </StrictMode>,
);
