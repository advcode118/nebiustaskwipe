import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/bricolage-grotesque/400.css";
import "@fontsource/bricolage-grotesque/600.css";
import "@fontsource/bricolage-grotesque/800.css";
import "./index.css";
import { App } from "./App.jsx";

createRoot(document.getElementById("root")).render(<App />);
