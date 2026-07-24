import { hexToHsl } from "@/utils/color";

const THEME_CACHE_KEY = "workspace-theme";

export function bootstrapWorkspaceTheme() {
  const root = document.documentElement;

  const cached = localStorage.getItem(THEME_CACHE_KEY);

  if (!cached) {
    return;
  }

  try {
    const theme = JSON.parse(cached);

    root.style.setProperty(
      "--workspace-primary",
      theme.primary
    );

    root.style.setProperty(
      "--workspace-secondary",
      theme.secondary
    );

    root.style.setProperty(
      "--primary",
      hexToHsl(theme.primary)
    );

    root.style.setProperty(
      "--secondary",
      hexToHsl(theme.secondary)
    );

    root.style.setProperty(
      "--ring",
      hexToHsl(theme.primary)
    );
  } catch {
    localStorage.removeItem(THEME_CACHE_KEY);
  }
}
