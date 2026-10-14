/**
 * The credential an email link carries in its fragment (`/reset-password#token=…`), read once.
 *
 * The fragment is cleared from the address bar as soon as it is read: until then the token sits
 * in browser history and in anything copied out of the address bar. Reading it is therefore not
 * repeatable, and a page that read it while rendering lost it whenever React rendered that page a
 * second time (a render it discarded and retried, or a remount), and then said the link was
 * invalid. Campaign session 4 (F-361): the first read is remembered for this page load, per path,
 * so every later read gets the same answer.
 */
const taken = new Map<string, URLSearchParams>();

export const takeFragmentParams = (): URLSearchParams => {
  const path = window.location.pathname;
  const fragment = window.location.hash.replace(/^#/, "");
  const params = new URLSearchParams(fragment);
  if (params.get("token")) {
    taken.set(path, params);
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    return params;
  }
  return taken.get(path) ?? params;
};
