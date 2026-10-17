import { useEffect } from "react";

const PRODUCT = "Servabo";

/**
 * The browser tab's title, most specific part first: "Documents · Operations · Servabo".
 *
 * Every tab and every history entry used to read "Servabo", so two open tabs (or the back
 * button's menu) could not be told apart. Empty parts are skipped; leaving the page restores the
 * product name, so a signed-out screen never shows the last tenant's page.
 */
export function useDocumentTitle(...parts: ReadonlyArray<string | null | undefined>): void {
  const title = [...parts.filter((part): part is string => Boolean(part && part.trim())), PRODUCT].join(" · ");
  useEffect(() => {
    document.title = title;
  }, [title]);
  useEffect(
    () => () => {
      document.title = PRODUCT;
    },
    [],
  );
}

export default useDocumentTitle;
