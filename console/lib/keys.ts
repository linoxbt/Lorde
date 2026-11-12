import "server-only";

import { createHash, randomBytes } from "node:crypto";

/** Same format and hash as gateway/keys.py: lorde_sk_ + 32 random bytes (base62); SHA-256(key + pepper). */
const ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

export function newKey(): string {
  let n = BigInt(`0x${randomBytes(32).toString("hex")}`);
  let s = "";
  while (n > 0n) { s = ALPHABET[Number(n % 62n)] + s; n /= 62n; }
  return `lorde_sk_${s.padStart(43, "0")}`;
}
export const prefixOf = (key: string) => key.slice(0, "lorde_sk_".length + 8);
export const hashKey = (key: string, pepper: string) => createHash("sha256").update(key + pepper).digest("hex");
