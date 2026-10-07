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
      className={`flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground ${className}`}
      aria-label="Toggle Theme"
      title={`Switch to ${theme === "light" ? "Dark" : "Light"} mode`}
    >
      {theme === "light" ? (
        <Moon className="h-4 w-4" />
      ) : (
        <Sun className="h-4 w-4" />
      )}
    </button>
  );
};

export default ThemeToggle;
