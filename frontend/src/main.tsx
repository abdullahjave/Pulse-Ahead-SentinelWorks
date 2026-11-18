import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { Shell } from "./components/Shell";
import { ErrorState, Loading } from "./components/ui";
import Impact from "./pages/Impact";
import Incidents from "./pages/Incidents";
import Overview from "./pages/Overview";
import Validation from "./pages/Validation";
import Workplaces from "./pages/Workplaces";
import { AppProvider, useApp } from "./state";

function Router() {
  const { route, user, meta, authError, asOf } = useApp();
  if (authError) return <div className="p-6"><ErrorState error={new Error(`${authError}. Is the backend running on port 8000?`)} /></div>;
  if (!user || !meta || !asOf) return <Loading label="Signing in to the demo…" className="h-full" />;
  const page = { overview: <Overview />, incidents: <Incidents />, workplaces: <Workplaces />, validation: <Validation />, impact: <Impact /> }[route.page];
  return <Shell>{page}</Shell>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppProvider><Router /></AppProvider>
  </StrictMode>,
);
