import React from "react";
import { Moon, Sun } from "lucide-react";

import { useUIStore } from "@/store/useUIStore";

export const ThemeToggle: React.FC<{ readonly className?: string }> = ({
  className = "",
}) => {
  const theme = useUIStore((state) => state.theme);
  const toggleTheme = useUIStore((state) => state.toggleTheme);

  return (
    <button
      type="button"
      onClick={toggleTheme}
      className={`rounded-lg border border-border bg-background p-2 text-muted-foreground transition-all hover:bg-muted/50 hover:text-foreground sm:p-2.5 ${className}`}
      aria-label="Toggle Theme"
      title={`Switch to ${theme === "light" ? "Dark" : "Light"} mode`}
    >
      {theme === "light" ? (
        <Moon className="h-4 w-4 sm:h-[1.125rem] sm:w-[1.125rem]" />
      ) : (
        <Sun className="h-4 w-4 sm:h-[1.125rem] sm:w-[1.125rem]" />
      )}
    </button>
  );
};

export default ThemeToggle;
