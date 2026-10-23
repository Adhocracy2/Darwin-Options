import { lazy, StrictMode, Suspense, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

// The 3D preview (#/3d) loads three.js only when opened.
const OfficePage = lazy(() => import("./three/OfficePage"));
const MetricsPage = lazy(() => import("./metrics/MetricsPage"));

function Root() {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const on = () => setHash(location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  if (hash.startsWith("#/3d")) return <Suspense fallback={null}><OfficePage /></Suspense>;
  if (hash.startsWith("#/metrics")) return <Suspense fallback={null}><MetricsPage /></Suspense>;
  return <App />;
}

createRoot(document.getElementById("root")!).render(<StrictMode><Root /></StrictMode>);
