import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Root from "./App";
import { ToastProvider } from "./components/ui";
import "highlight.js/styles/github.css";
import "./hljs-dark.css";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ToastProvider>
      <Root />
    </ToastProvider>
  </StrictMode>,
);
