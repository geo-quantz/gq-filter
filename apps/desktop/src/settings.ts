// The gqfilter path the user chose, kept in this machine's WebView storage.
// Storage can be unavailable; the app then works without remembering it.

const CLI_PATH_KEY = "gq-filter.cliPath";

export function loadCliPath(): string {
  try {
    return window.localStorage.getItem(CLI_PATH_KEY) ?? "";
  } catch {
    return "";
  }
}

export function saveCliPath(path: string): void {
  try {
    if (path.trim() === "") window.localStorage.removeItem(CLI_PATH_KEY);
    else window.localStorage.setItem(CLI_PATH_KEY, path.trim());
  } catch {
    // Not remembered this time; the setting still applies until the app closes.
  }
}
