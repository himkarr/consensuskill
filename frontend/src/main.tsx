import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/roboto/wght.css";
import "@fontsource-variable/roboto-mono/wght.css";
import App from "./App";
import { installRipple } from "./ripple";
import "./styles.css";

installRipple();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
