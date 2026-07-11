import React from "react";
import { Settings as SettingsIcon } from "lucide-react";

/**
 * Presentation-only, stateless Settings view component for FlowPilot AI.
 *
 * Provides structural placeholder layouts for future workspace configurations,
 * account preferences, and integrations, unblocking compile-time routers.
 */
export const Settings: React.FC = () => {
  return (
    <div className="space-y-6">
      {/* --- Section 1: Page Header --- */}
      <header className="space-y-1 select-none">
        <h1 className="text-2xl font-extrabold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground font-semibold leading-relaxed">
          Configure your FlowPilot AI workspace and account preferences.
        </p>
      </header>

      {/* --- Section 2: Placeholder Settings Panel Card --- */}
      <div
        className="bg-card border border-border/60 dark:border-border/40 rounded-xl p-8 max-w-lg mx-auto shadow-sm flex flex-col items-center text-center space-y-5 select-none animate-scale-in"
        role="region"
        aria-labelledby="settings-card-title"
      >
        {/* Visual Settings Spinning Icon */}
        <div className="p-3.5 bg-primary/10 text-primary rounded-full animate-spin-slow">
          <SettingsIcon className="h-6 w-6 flex-shrink-0" />
        </div>

        {/* Informative message labels */}
        <div className="space-y-2 max-w-sm">
          <h2
            id="settings-card-title"
            className="text-sm font-extrabold tracking-tight text-foreground"
          >
            Workspace Settings
          </h2>
          <p className="text-xs text-muted-foreground font-semibold leading-relaxed">
            Workspace configuration, account preferences, notification preferences,
            API integrations, and security settings will be available in a future release.
          </p>
        </div>

        {/* Coming Soon status badge */}
        <span className="inline-flex items-center px-2.5 py-1 rounded-md text-[10px] font-black tracking-wider bg-primary/15 text-primary leading-none uppercase select-none">
          Coming Soon
        </span>
      </div>
    </div>
  );
};

export default Settings;
