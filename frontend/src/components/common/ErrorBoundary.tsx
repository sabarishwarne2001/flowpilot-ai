import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

import { AlertOctagon, RefreshCw, RotateCcw } from "lucide-react";

import { isStaleBundleError, reloadOnceForStaleBundle } from "@/utils/staleBundle";

interface ErrorBoundaryProps {
  readonly children: ReactNode;
  readonly fallback?: ReactNode;
  /**
   * "app" (default) replaces the whole screen; "page" renders inside the
   * layout's content area, so the sidebar and header stay usable.
   */
  readonly variant?: "app" | "page";
  /** A page boundary resets when this changes (the route), not on every re-render. */
  readonly resetKey?: string;
}


interface ErrorBoundaryState {
  readonly hasError: boolean;
  readonly error: Error | null;
}

export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);

    this.state = {
      hasError: false,
      error: null,
    };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return {
      hasError: true,
      error,
    };
  }

  override componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    this.logError(error, errorInfo);
    if (isStaleBundleError(error)) {
      reloadOnceForStaleBundle();
    }
  }

  override componentDidUpdate(prevProps: ErrorBoundaryProps): void {
    if (!this.state.hasError) {return;}
    if (this.props.variant === "page") {
      // The layout re-renders on every poll; only a new route clears a page error.
      if (prevProps.resetKey !== this.props.resetKey) {
        this.resetBoundary();
      }
      return;
    }
    if (prevProps.children !== this.props.children) {
      this.resetBoundary();
    }
  }

  private logError(error: Error, errorInfo: ErrorInfo): void {
    // Future integrations:
    // Sentry.captureException(...)
    // OpenTelemetry...

    if (import.meta.env.DEV) {
      console.error("React Error Boundary", error, errorInfo);
    }
  }

  private resetBoundary = (): void => {
    this.setState({
      hasError: false,
      error: null,
    });
  };

  private handleRecovery = (): void => {
    try {
      this.resetBoundary();
    } catch {
      window.location.reload();
    }
  };

  override render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }

    if (this.props.fallback) {
      return this.props.fallback;
    }

    const stale = isStaleBundleError(this.state.error);

    if (this.props.variant === "page") {
      return (
        <div role="alert" className="mx-auto mt-10 flex w-full max-w-lg flex-col items-center fp-card p-8 text-center">
          <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-destructive/10 text-destructive">
            {stale ? <RefreshCw className="h-5 w-5" aria-hidden /> : <AlertOctagon className="h-5 w-5" aria-hidden />}
          </div>
          <h2 className="mb-1.5 text-lg font-semibold tracking-tight text-foreground">
            {stale ? "A new version of FlowPilot is available" : "Something went wrong on this page"}
          </h2>
          <p className="mb-5 text-sm leading-relaxed text-muted-foreground">
            {stale
              ? "FlowPilot was updated while this tab was open. Reload to continue with the new version."
              : "This page hit an unexpected error. Your data is safe, and the rest of FlowPilot still works: try again, or pick another page from the menu."}
          </p>
          {import.meta.env.DEV && this.state.error && !stale && (
            <pre className="mb-5 max-h-32 w-full overflow-auto rounded-lg border border-border/40 bg-muted/60 p-3 text-left font-mono text-[11px] text-destructive">
              {this.state.error.name}: {this.state.error.message}
            </pre>
          )}
          <div className="flex flex-wrap justify-center gap-2">
            {!stale && (
              <button type="button" onClick={this.handleRecovery} className="fp-btn fp-btn-primary">
                <RotateCcw className="h-4 w-4" aria-hidden />
                Try again
              </button>
            )}
            <button
              type="button"
              onClick={() => window.location.reload()}
              className={stale ? "fp-btn fp-btn-primary" : "fp-btn fp-btn-secondary"}
            >
              <RefreshCw className="h-4 w-4" aria-hidden />
              Reload page
            </button>
          </div>
        </div>
      );
    }

    return (
      <div
        role="alert"
        className="flex min-h-dvh w-full items-center justify-center bg-background p-6 text-foreground"
      >
        <div className="flex w-full max-w-md flex-col items-center fp-card p-8 text-center shadow-lg">
          <div className="mb-6 flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10 text-destructive">
            <AlertOctagon className="h-6 w-6" />
          </div>

          <h2 className="mb-2 text-xl font-semibold tracking-tight">
            Something went wrong
          </h2>

          <p className="mb-6 text-sm font-medium leading-relaxed text-muted-foreground">
            An unexpected application error occurred. Your data is safe. You can
            retry or refresh the application.
          </p>

          {import.meta.env.DEV && this.state.error && (
            <div className="mb-6 max-h-40 w-full overflow-auto rounded-lg border border-border/40 bg-muted/60 p-3 text-left font-mono text-[11px] text-destructive dark:bg-muted/10">
              <p className="font-semibold">
                {this.state.error.name}: {this.state.error.message}
              </p>

              <pre className="mt-2 whitespace-pre-wrap break-words opacity-80">
                {this.state.error.stack}
              </pre>
            </div>
          )}

          <button
            type="button"
            onClick={this.handleRecovery}
            className="fp-btn-primary flex w-full items-center justify-center rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground transition-all hover:bg-primary/95 active:scale-[0.98]"
          >
            <RotateCcw className="mr-2 h-4 w-4" />
            Recover and Retry
          </button>
        </div>
      </div>
    );
  }
}

export default ErrorBoundary;
