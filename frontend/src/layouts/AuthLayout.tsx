import { Outlet } from "react-router-dom";

const APP_NAME = import.meta.env.VITE_APP_NAME ?? "FlowPilot AI";

export const AuthLayout = () => {
  return (
    <main className="min-h-dvh grid grid-cols-1 lg:grid-cols-12 bg-background text-foreground transition-colors duration-200">
      {/* ===========================
          Authentication Area
      =========================== */}

      <section className="relative flex flex-col justify-center px-6 py-12 sm:px-12 xl:px-16 lg:col-span-5">
        <header
          aria-label="Application Logo"
          className="absolute left-6 top-8 flex items-center gap-2 select-none sm:left-12 xl:left-16"
        >
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary shadow-md">
            <span className="font-mono text-xl font-black text-primary-foreground">
              FP
            </span>
          </div>

          <span className="text-xl font-black tracking-tight">
            {APP_NAME.replace(" AI", "")}
            <span className="text-primary">AI</span>
          </span>
        </header>

        <div className="mx-auto mt-8 w-full max-w-md">
          <Outlet />
        </div>
      </section>

      {/* ===========================
          Hero Section
      =========================== */}

      <aside className="relative hidden overflow-hidden border-l border-border bg-muted/50 p-12 dark:bg-muted/10 lg:col-span-7 lg:flex lg:flex-col lg:justify-between xl:p-16">
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-tr from-primary/10 via-transparent to-transparent" />

        <div className="pointer-events-none absolute -right-12 -top-12 h-96 w-96 rounded-full bg-primary/5 blur-3xl" />

        <div className="z-10 mt-auto max-w-lg">
          <h2 className="mb-4 text-3xl font-extrabold tracking-tight xl:text-4xl">
            Document intelligence and workflow automation at scale.
          </h2>

          <p className="text-sm font-medium leading-relaxed text-muted-foreground xl:text-base">
            Eliminate manual workflows with AI-powered document processing,
            semantic search, automation, and intelligent assistants built for
            modern businesses.
          </p>
        </div>

        <footer className="z-10 mt-8 flex items-center justify-between border-t border-border/40 pt-8 text-xs text-muted-foreground/60">
          <span>
            © {new Date().getFullYear()} {APP_NAME}
          </span>

          <span>All rights reserved.</span>
        </footer>
      </aside>
    </main>
  );
};

export default AuthLayout;
