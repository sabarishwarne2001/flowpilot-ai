import React from "react";
import { Link } from "react-router-dom";
import { Compass, ArrowLeft } from "lucide-react";
import { ROUTES } from "@/constants/routes";

/**
 * 404 Fallback page view for FlowPilot AI client router.
 *
 * Intercepts invalid route allocations, and redirects client sessions
 * back to the dashboard portal workspace safely.
 */
export const NotFound: React.FC = () => {
  return (
    <div
      className="min-h-dvh w-full flex items-center justify-center p-6 bg-background text-foreground transition-colors duration-200"
      role="main"
      aria-labelledby="notfound-title"
    >
      {/* Centralized NotFound Card Frame */}
      <div className="w-full max-w-md bg-card border border-border/60 dark:border-border/40 rounded-xl p-8 shadow-lg text-center flex flex-col items-center select-none animate-scale-in">
        {/* Visual Navigation Compass Icon Container */}
        <div className="h-14 w-14 rounded-full bg-primary/10 text-primary flex items-center justify-center mb-6">
          <Compass className="h-7 w-7 animate-pulse" />
        </div>

        {/* Diagnostic Descriptions */}
        <span className="text-[11px] font-black uppercase tracking-widest text-primary mb-2">
          Error 404
        </span>

        <h1
          id="notfound-title"
          className="text-xl font-extrabold tracking-tight mb-2"
        >
          Page not found
        </h1>

        <p className="text-xs text-muted-foreground font-semibold leading-relaxed mb-8 max-w-xs">
          The requested URL path does not exist in this workspace. It may have been renamed or access is denied.
        </p>

        {/* Safe navigation link to Dashboard */}
        <Link
          to={ROUTES.DASHBOARD}
          className="w-full flex items-center justify-center px-4 py-2.5 bg-primary text-primary-foreground font-bold text-xs rounded-lg hover:bg-primary/95 transition-all shadow-sm active:scale-[0.98] outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          <ArrowLeft className="h-3.5 w-3.5 mr-2" />
          <span>Return to Dashboard</span>
        </Link>
      </div>
    </div>
  );
};

export default NotFound;
