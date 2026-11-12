// Test helper: decrypts a dashboard.enc.json (argv[2]) with env DASHBOARD_PASSPHRASE using WebCrypto,
// the same calls the browser makes. Prints the plaintext, or exits 1 on a wrong passphrase.
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";

const b = JSON.parse(readFileSync(process.argv[2], "utf8"));
const un = (s) => Uint8Array.from(Buffer.from(s, "base64"));
const enc = new TextEncoder();
const base = await webcrypto.subtle.importKey("raw", enc.encode(process.env.DASHBOARD_PASSPHRASE || ""), "PBKDF2", false, ["deriveKey"]);
const key = await webcrypto.subtle.deriveKey(
  { name: "PBKDF2", salt: un(b.salt), iterations: b.iterations, hash: b.hash },
  base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
try {
  const plain = await webcrypto.subtle.decrypt({ name: "AES-GCM", iv: un(b.iv) }, key, un(b.ct));
  process.stdout.write(Buffer.from(plain));
} catch { process.exit(1); }
