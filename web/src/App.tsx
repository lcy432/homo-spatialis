import { Navigate, Route, Routes, useParams } from "react-router";
import { AppShell, PageTitle } from "./components/Layout";
import { ConfirmPage } from "./pages/ConfirmPage";
import { DevSamples, SampleGallery } from "./pages/DevPages";
import { FillFacts } from "./pages/FillFacts";
import { FillHealth } from "./pages/FillHealth";
import { FillPriorities } from "./pages/FillPriorities";
import { ClonePage, ProgressPage, ResultPage } from "./pages/RunPages";

function FillStep() {
  const { step } = useParams();
  switch (step) {
    case "1":
      return <FillFacts />;
    case "2":
      return <FillPriorities />;
    case "3":
      return <FillHealth />;
    case "4":
      return <ConfirmPage />;
    default:
      return <Navigate to="/fill/1" replace />;
  }
}

function NotFound() {
  return (
    <div>
      <PageTitle>找不到这一页</PageTitle>
      <p>
        <a className="tap" href="/fill/1">回到第 1 步</a>
      </p>
    </div>
  );
}

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/fill/1" replace />} />
        <Route path="fill/:step" element={<FillStep />} />
        <Route path="runs/:runId" element={<ProgressPage />} />
        <Route path="runs/:runId/result" element={<ResultPage />} />
        <Route path="runs/:runId/clones/:cloneRef" element={<ClonePage />} />
        <Route path="dev/samples" element={<DevSamples />} />
        <Route path="dev/samples/:id" element={<SampleGallery />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
