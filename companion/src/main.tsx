import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { LangProvider } from "./i18n";
import "./styles.css";
import { invoke } from "@tauri-apps/api/core";

function reportFrontendCrash(reason: unknown) {
  const error = reason instanceof Error ? reason : new Error(String(reason));
  void invoke("report_frontend_crash", {
    message: error.message,
    stack: error.stack ?? "",
  }).catch(() => {});
}

window.addEventListener("error", (event) => {
  reportFrontendCrash(event.error ?? `${event.message} (${event.filename}:${event.lineno}:${event.colno})`);
});
window.addEventListener("unhandledrejection", (event) => reportFrontendCrash(event.reason));

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <LangProvider>
      <App />
    </LangProvider>
  </React.StrictMode>
);
