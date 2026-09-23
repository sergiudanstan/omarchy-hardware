// Node-RED settings for the omarchy-hardware MING example.
//
// Two identities:
//   admin  -- you, in the editor, with every permission.
//   claude -- the plugin's static API token, limited to reading flows and
//             pressing inject buttons ("read" + "inject.write"). It cannot
//             deploy flows, install nodes or change settings, so the plugin's
//             refusal to deploy flows is enforced by Node-RED as well.
//
// Both secrets, and the credential secret, are files bootstrap.sh creates under
// secrets/ and compose.yaml mounts read-only into /run/secrets.

const crypto = require("crypto");
const fs = require("fs");

// Mounted by compose.yaml from secrets/. A missing file leaves the value empty,
// and an empty secret never matches (see same()), so nothing is left open.
function secret(name) {
  try {
    return fs.readFileSync(`/run/secrets/${name}`, "utf8").trim();
  } catch (err) {
    return "";
  }
}
const ADMIN_PASSWORD = secret("nodered-admin-password");
const API_TOKEN = secret("nodered-token");

function same(given, expected) {
  // Constant-time comparison; an empty expected value never matches.
  const a = Buffer.from(String(given));
  const b = Buffer.from(expected);
  return b.length > 0 && a.length === b.length && crypto.timingSafeEqual(a, b);
}

const ADMIN = { username: "admin", permissions: "*" };
const CLAUDE = { username: "claude", permissions: ["read", "inject.write"] };

module.exports = {
  uiPort: 1880,
  flowFile: "flows.json",
  credentialSecret: secret("nodered-credential-secret"),

  https: {
    key: fs.readFileSync("/etc/ming/certs/server.key"),
    cert: fs.readFileSync("/etc/ming/certs/server.crt"),
  },
  requireHttps: true,

  adminAuth: {
    type: "credentials",
    users: async (username) => (username === "admin" ? ADMIN : null),
    authenticate: async (username, password) =>
      username === "admin" && same(password, ADMIN_PASSWORD) ? ADMIN : null,
    tokens: async (token) => (same(token, API_TOKEN) ? CLAUDE : null),
  },

  // Function nodes may not pull npm modules at runtime.
  functionExternalModules: false,
  editorTheme: { projects: { enabled: false } },
  diagnostics: { enabled: false },
  runtimeState: { enabled: false },
  logging: { console: { level: "info", metrics: false, audit: false } },
};
