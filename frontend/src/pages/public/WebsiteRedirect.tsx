/**
 * Website session (B7): /pricing, /terms and /privacy moved to the public website. Old links and
 * bookmarks to the app's addresses land here and are sent on to the website's page; the link
 * stays on screen for a browser that blocks the redirect.
 */
import React, { useEffect } from "react";

import { Brand } from "@/components/branding/Brand";

export const WebsiteRedirect: React.FC<{ readonly to: string; readonly title: string }> = ({ to, title }) => {
  useEffect(() => {
    window.location.replace(to);
  }, [to]);

  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-6 bg-background px-5 text-center text-foreground">
      <Brand variant="login" />
      <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
      <p className="text-sm text-muted-foreground">
        This page is on our website:{" "}
        <a href={to} className="font-medium text-primary underline-offset-4 hover:underline dark:text-[hsl(var(--primary-on-dark))]">
          {to.replace(/^https?:\/\//, "")}
        </a>
      </p>
    </main>
  );
};

export default WebsiteRedirect;
