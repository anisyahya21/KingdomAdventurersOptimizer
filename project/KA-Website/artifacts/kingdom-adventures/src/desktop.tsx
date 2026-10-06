import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import StrategyOptimizerDesktopPage from "./pages/strategy-optimizer-desktop";
import "./index.css";
import { TooltipProvider } from "@/components/ui/tooltip";
import NativeLanguageOptimizerPanel from "@/components/native-language-optimizer-panel";
import OptimizerEngineSelector from "@/components/optimizer-engine-selector";

/**
 * Desktop-only entry, rendered by the local pywebview host instead of the website router.
 *
 * It mirrors the provider that `src/main.tsx` installs for the browser app (React Query) but skips
 * the PWA service worker and the Vercel analytics beacon, which make no sense inside an embedded
 * desktop window and would try to reach the network. The optimiser talks to the host only through
 * `window.pywebview.api`; everything else it renders comes from the shared design system.
 */
const queryClient = new QueryClient();
const nativeLanguages = new URLSearchParams(window.location.search).has("native-languages");
const fullLanguages = new URLSearchParams(window.location.search).has("languages");

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>{nativeLanguages
      ? <div className="min-h-screen bg-background text-foreground p-6"><div className="mx-auto max-w-6xl"><NativeLanguageOptimizerPanel /></div></div>
      : <>{fullLanguages ? <div className="mx-auto max-w-[1600px] px-4 pt-4"><OptimizerEngineSelector /></div> : null}<StrategyOptimizerDesktopPage /></>}</TooltipProvider>
  </QueryClientProvider>,
);
