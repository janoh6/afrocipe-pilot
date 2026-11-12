// Encrypts stdin with AES-256-GCM. Key: PBKDF2-HMAC-SHA256 from env DASHBOARD_PASSPHRASE.
// Output (stdout): JSON { v, kdf, hash, iterations, salt, iv, ct } with base64 fields.
// ct is ciphertext followed by the 16-byte tag, the layout WebCrypto expects.
import { pbkdf2Sync, randomBytes, createCipheriv } from "node:crypto";

const pass = process.env.DASHBOARD_PASSPHRASE;
if (!pass) { console.error("passphrase missing"); process.exit(2); }
const chunks = [];
for await (const c of process.stdin) chunks.push(c);
const plain = Buffer.concat(chunks);
const iterations = 600000;
const salt = randomBytes(16);
const iv = randomBytes(12);
const key = pbkdf2Sync(pass, salt, iterations, 32, "sha256");
const cipher = createCipheriv("aes-256-gcm", key, iv);
const ct = Buffer.concat([cipher.update(plain), cipher.final(), cipher.getAuthTag()]);
process.stdout.write(JSON.stringify({
  v: 1, kdf: "PBKDF2", hash: "SHA-256", iterations,
  salt: salt.toString("base64"), iv: iv.toString("base64"), ct: ct.toString("base64"),
}));
