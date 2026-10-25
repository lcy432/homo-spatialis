import { type ReactNode, Suspense, lazy } from "react";
import { useTranslation } from "react-i18next";
import { Route, Routes } from "react-router";
import { AppShell, PageTitle } from "./components/Layout";
import { HomePage } from "./pages/HomePage";
import { IntentPage } from "./pages/IntentPage";
import { RunsPage } from "./pages/RunsPage";
import { InvitePage, RequireSession } from "./pages/SessionPages";
import { SettingsPage } from "./pages/SettingsPage";
import { StartPage } from "./pages/StartPage";
import { DraftRoute } from "./pages/draft/DraftRoute";
import { CityPage, ClonePage, ResultsPage } from "./pages/results/ResultsPages";

function NotFound() {
  const { t } = useTranslation();
  return (
    <div>
      <PageTitle>404</PageTitle>
      <p>
        <a className="tap" href="/">
          {t("app.nav.home")}
        </a>
      </p>
    </div>
  );
}

const guard = (node: ReactNode) => <RequireSession>{node}</RequireSession>;

// 样例画廊只在开发构建里注册（正式构建里这段代码与全部样例都会被去掉）
const GalleryIndex = import.meta.env.DEV ? lazy(() => import("./pages/dev/Gallery").then((m) => ({ default: m.GalleryIndex }))) : null;
const GallerySample = import.meta.env.DEV ? lazy(() => import("./pages/dev/Gallery").then((m) => ({ default: m.GallerySample }))) : null;
const dev = (node: ReactNode) => <Suspense fallback={null}>{node}</Suspense>;

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="invite" element={<InvitePage />} />
        <Route index element={guard(<HomePage />)} />
        <Route path="start" element={guard(<StartPage />)} />
        <Route path="draft/:draftId/:step" element={guard(<DraftRoute />)} />
        <Route path="intents/:intentId" element={guard(<IntentPage />)} />
        <Route path="results/:intentId" element={guard(<ResultsPage />)} />
        <Route path="results/:intentId/city/:cityRef" element={guard(<CityPage />)} />
        <Route path="results/:intentId/clone/:cloneRef" element={guard(<ClonePage />)} />
        <Route path="runs" element={guard(<RunsPage />)} />
        <Route path="settings" element={guard(<SettingsPage />)} />
        {GalleryIndex && GallerySample ? (
          <>
            <Route path="dev/gallery" element={dev(<GalleryIndex />)} />
            <Route path="dev/gallery/:id" element={dev(<GallerySample />)} />
            <Route path="dev/gallery/:id/city/:cityRef" element={dev(<GallerySample />)} />
            <Route path="dev/gallery/:id/clone/:cloneRef" element={dev(<GallerySample />)} />
          </>
        ) : null}
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
