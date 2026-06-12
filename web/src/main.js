import { mount } from "svelte";
import "./app.css";
import App from "./App.svelte";
import { initialResolvedTheme } from "./lib/theme.svelte.js";

// Apply the theme before mount so there's no flash of the wrong palette.
document.documentElement.dataset.theme = initialResolvedTheme();

const app = mount(App, { target: document.getElementById("app") });

export default app;
