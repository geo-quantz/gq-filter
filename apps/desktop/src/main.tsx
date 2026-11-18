import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { tauriApi } from "./api";
import { App } from "./App";
import "./styles.css";

const root = document.getElementById("root");
if (root === null) throw new Error("root element is missing");

createRoot(root).render(
  <StrictMode>
    <App api={tauriApi} />
  </StrictMode>,
);
