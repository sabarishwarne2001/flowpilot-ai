/**
 * A lazily loaded page whose file no longer exists: the app was deployed again
 * while this tab was open, and the old bundle names chunks the server no longer
 * has. Retrying cannot help; loading the new version does.
 */
export function isStaleBundleError(error: unknown): boolean {
  const message = error instanceof Error ? `${error.name} ${error.message}` : String(error ?? "");
  return /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|ChunkLoadError|Loading chunk [\w-]+ failed/i.test(
    message,
  );
}

const STALE_RELOAD_KEY = "fp:stale-bundle-reload";

/** Reload once for a stale bundle; a second failure within a minute shows the card instead. */
export function reloadOnceForStaleBundle(): boolean {
  try {
    const last = Number(window.sessionStorage.getItem(STALE_RELOAD_KEY) ?? "0");
    if (Date.now() - last < 60_000) {return false;}
    window.sessionStorage.setItem(STALE_RELOAD_KEY, String(Date.now()));
  } catch {
    return false;
  }
  window.location.reload();
  return true;
}
