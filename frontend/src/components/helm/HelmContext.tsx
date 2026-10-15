import React, { createContext, useCallback, useContext, useEffect, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import type { HelmAccess, HelmBook, HelmOverview } from "@/types/helm";

export interface HelmContextValue {
  readonly organizationId: string;
  readonly orgSlug: string;
  readonly organizationName: string;
  readonly role: string;
  readonly overview: HelmOverview;
  readonly access: HelmAccess;
  /** Owners and admins change Helm; the billing role reads and approves payment actions. */
  readonly canDecide: boolean;
  readonly books: readonly HelmBook[];
  /** The book on screen: `?book=`, else the remembered one, else the first real book, else the sample. */
  readonly book: HelmBook | null;
  readonly selectBook: (bookId: string) => void;
  /** A Helm path with the selected book kept in its query string. */
  readonly withBook: (path: string) => string;
}

const HelmContext = createContext<HelmContextValue | null>(null);

export const useHelm = (): HelmContextValue => {
  const value = useContext(HelmContext);
  if (!value) {
    throw new Error("useHelm must be used inside HelmLayout (ROUTE_PATTERNS.helmShell).");
  }
  return value;
};

/** The selected book; pages that need one render only when it exists (HelmLayout sends others to Start). */
export const useHelmBook = (): HelmBook => {
  const { book } = useHelm();
  if (!book) {
    throw new Error("useHelmBook needs a selected Helm book.");
  }
  return book;
};

const bookKey = (organizationId: string): string => `helm.book.${organizationId}`;

const readBook = (organizationId: string): string | null => {
  try {
    return window.localStorage.getItem(bookKey(organizationId));
  } catch {
    return null;
  }
};

const writeBook = (organizationId: string, bookId: string): void => {
  try {
    window.localStorage.setItem(bookKey(organizationId), bookId);
  } catch {
    // A convenience only.
  }
};

export const HelmProvider: React.FC<{
  readonly organizationId: string;
  readonly orgSlug: string;
  readonly organizationName: string;
  readonly role: string;
  readonly overview: HelmOverview;
  readonly children: React.ReactNode;
}> = ({ organizationId, orgSlug, organizationName, role, overview, children }) => {
  const [params, setParams] = useSearchParams();
  const requested = params.get("book");
  const books = overview.books;

  const book = useMemo<HelmBook | null>(() => {
    const byId = (id: string | null) => (id ? books.find((b) => b.id === id) : undefined);
    return (
      byId(requested) ??
      byId(readBook(organizationId)) ??
      books.find((b) => b.kind !== "SAMPLE") ??
      books[0] ??
      null
    );
  }, [books, organizationId, requested]);

  useEffect(() => {
    if (book) {
      writeBook(organizationId, book.id);
    }
  }, [book, organizationId]);

  const selectBook = useCallback(
    (bookId: string) => {
      writeBook(organizationId, bookId);
      const next = new URLSearchParams(params);
      next.set("book", bookId);
      setParams(next);
    },
    [organizationId, params, setParams],
  );

  const withBook = useCallback(
    (path: string) => (book ? `${path}${path.includes("?") ? "&" : "?"}book=${encodeURIComponent(book.id)}` : path),
    [book],
  );

  const value = useMemo<HelmContextValue>(
    () => ({
      organizationId,
      orgSlug,
      organizationName,
      role,
      overview,
      access: overview.access,
      canDecide: overview.can_decide,
      books,
      book,
      selectBook,
      withBook,
    }),
    [book, books, organizationId, organizationName, orgSlug, overview, role, selectBook, withBook],
  );

  return <HelmContext.Provider value={value}>{children}</HelmContext.Provider>;
};
