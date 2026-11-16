import { useCallback, useEffect, useState } from "react";

export type Theme = "dark" | "light";

const STORAGE_KEY = "keysight-connect.theme";
const EVT = "keysight-theme-change";

function resolveSystem(): Theme {
  try {
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function getTheme(): Theme {
  const attr = document.documentElement.dataset.theme;
  if (attr === "light" || attr === "dark") return attr;
  return resolveSystem();
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (meta) meta.content = theme === "light" ? "#f4f6f9" : "#060a0f";
}

export function setTheme(theme: Theme): void {
  applyTheme(theme);
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* storage may be unavailable */
  }
  window.dispatchEvent(new Event(EVT));
}

export function toggleTheme(): Theme {
  const next: Theme = getTheme() === "dark" ? "light" : "dark";
  setTheme(next);
  return next;
}

export function useTheme(): Theme {
  const [theme, setThemeState] = useState<Theme>(getTheme);

  useEffect(() => {
    const onChange = () => setThemeState(getTheme());
    window.addEventListener(EVT, onChange);
    return () => window.removeEventListener(EVT, onChange);
  }, []);

  return theme;
}

export function useThemeToggle(): [Theme, () => void] {
  const theme = useTheme();
  const toggle = useCallback(() => toggleTheme(), []);
  return [theme, toggle];
}
