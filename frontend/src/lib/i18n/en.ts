// English — the source-of-truth catalog. de.ts / fr.ts mirror these keys.
// Keys are dotted paths grouped by surface. Keep additions here first,
// then translate in the other two files.
export const en: Record<string, string> = {
  // Navigation
  "nav.home": "Home",
  "nav.camera": "Camera",
  "nav.gallery": "Gallery",
  "nav.schedule": "Schedule",
  "nav.destinations": "Destinations",
  "nav.terminal": "Terminal",
  "nav.settings": "Settings",

  // Common actions / labels
  "common.save": "Save",
  "common.saving": "Saving…",
  "common.cancel": "Cancel",
  "common.delete": "Delete",
  "common.retry": "Retry",
  "common.refresh": "Refresh",
  "common.add": "Add",
  "common.close": "Close",
  "common.back": "Back",
  "common.loading": "Loading…",

  // Connection / offline banner
  "conn.lost": "Connection to the station lost — retrying…",
  "conn.retryNow": "Retry now",

  // Status words
  "status.online": "Online",
  "status.offline": "Offline",
  "status.connecting": "Connecting",
  "status.active": "Active",
  "status.paused": "Paused",
  "status.warning": "Warning",

  // Home / overview
  "home.title": "Station overview",
  "home.status": "Status",
  "home.capturesToday": "Captures today",
  "home.queue": "Queue",
  "home.storage": "Storage",
  "home.cpu": "CPU",
  "home.memory": "Memory",
  "home.network": "Network",
  "home.ups": "UPS",
  "home.recentActivity": "Recent activity",
  "home.polled": "Polled",

  // Login
  "login.title": "Enter PIN",
  "login.subtitle": "Unlock the station cockpit",
  "login.unlock": "Unlock",
  "login.wrongPin": "Incorrect PIN",

  // Settings
  "settings.language": "Language",
};

export type MessageKey = keyof typeof en;
