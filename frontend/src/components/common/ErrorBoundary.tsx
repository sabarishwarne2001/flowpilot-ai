import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

import { AlertOctagon, RotateCcw } from "lucide-react";

interface ErrorBoundaryProps {
  readonly children: ReactNode;
  readonly fallback?: ReactNode;
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
  }

  override componentDidUpdate(prevProps: ErrorBoundaryProps): void {
    if (this.state.hasError && prevProps.children !== this.props.children) {
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

    return (
      <div
        role="alert"
        className="flex min-h-dvh w-full items-center justify-center bg-background p-6 text-foreground"
      >
        <div className="flex w-full max-w-md flex-col items-center rounded-xl border border-border bg-card p-8 text-center shadow-lg">
          <div className="mb-6 flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10 text-destructive">
            <AlertOctagon className="h-6 w-6" />
          </div>

          <h2 className="mb-2 text-xl font-black tracking-tight">
            Something went wrong
          </h2>

          <p className="mb-6 text-sm font-medium leading-relaxed text-muted-foreground">
            An unexpected application error occurred. Your data is safe. You can
            retry or refresh the application.
          </p>

          {import.meta.env.DEV && this.state.error && (
            <div className="mb-6 max-h-40 w-full overflow-auto rounded-lg border border-border/40 bg-muted/60 p-3 text-left font-mono text-[11px] text-destructive dark:bg-muted/10">
              <p className="font-bold">
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
            className="flex w-full items-center justify-center rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground shadow-sm transition-all hover:bg-primary/95 active:scale-[0.98]"
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
