import { createVerify, randomUUID, verify } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { app } from "electron";
import { LICENSE_ED25519_PUBLIC_KEY, LICENSE_PUBLIC_KEY } from "./license-public-key.js";

type LicensePayload = {
  version: 1;
  tier: "pro";
  installId: string;
  issuedAt: string;
  expiresAt?: string;
  maxConcurrentRunners?: number;
};

type LicenseStore = {
  installId: string;
  code?: string;
};

const COMPACT_PREFIX = "P2";
const COMPACT_PAYLOAD_SIZE = 23;
const COMPACT_SIGNATURE_SIZE = 64;

export type LicenseStatus = {
  installId: string;
  tier: "free" | "pro";
  valid: boolean;
  maxConcurrentRunners: number;
  expiresAt?: string;
  message: string;
};

function licenseFile() {
  return path.join(app.getPath("userData"), "license.json");
}

function readStore(): LicenseStore {
  try {
    const parsed = JSON.parse(readFileSync(licenseFile(), "utf8")) as Partial<LicenseStore>;
    if (typeof parsed.installId === "string" && parsed.installId) {
      return { installId: parsed.installId, ...(typeof parsed.code === "string" ? { code: parsed.code } : {}) };
    }
  } catch {
    // A new installation simply receives a new anonymous installation ID.
  }
  const store = { installId: randomUUID() };
  writeStore(store);
  return store;
}

function writeStore(store: LicenseStore) {
  writeFileSync(licenseFile(), `${JSON.stringify(store, null, 2)}\n`, "utf8");
}

function freeStatus(installId: string, message: string): LicenseStatus {
  return { installId, tier: "free", valid: false, maxConcurrentRunners: 1, message };
}

function statusForPro(installId: string, maxConcurrentRunners: number, expiresAt?: string): LicenseStatus {
  return {
    installId,
    tier: "pro",
    valid: true,
    maxConcurrentRunners,
    ...(expiresAt ? { expiresAt } : {}),
    message: expiresAt ? `专业版有效至 ${expiresAt}` : "专业版永久有效",
  };
}

function installationIdBytes(installId: string) {
  const compact = installId.replace(/-/g, "");
  if (!/^[a-f0-9]{32}$/i.test(compact)) throw new Error("当前安装 ID 无效");
  return Buffer.from(compact, "hex");
}

function isoDateFromDay(day: number) {
  return new Date(day * 86_400_000).toISOString().slice(0, 10);
}

function expiryTimestamp(date: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!match) throw new Error("授权码到期时间无效");
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const timestamp = Date.UTC(year, month - 1, day);
  if (Number.isNaN(timestamp) || new Date(timestamp).toISOString().slice(0, 10) !== date) {
    throw new Error("授权码到期时间无效");
  }
  return timestamp + 86_400_000 - 1;
}

function decodePayload(encoded: string): LicensePayload {
  const raw = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8")) as Partial<LicensePayload>;
  if (
    raw.version !== 1
    || raw.tier !== "pro"
    || typeof raw.installId !== "string"
    || !raw.installId
    || typeof raw.issuedAt !== "string"
  ) {
    throw new Error("授权码内容无效");
  }
  if (raw.expiresAt !== undefined && (typeof raw.expiresAt !== "string" || Number.isNaN(Date.parse(raw.expiresAt)))) {
    throw new Error("授权码到期时间无效");
  }
  if (
    raw.maxConcurrentRunners !== undefined
    && (!Number.isInteger(raw.maxConcurrentRunners) || raw.maxConcurrentRunners < 2 || raw.maxConcurrentRunners > 10)
  ) {
    throw new Error("授权码并发配额无效");
  }
  return raw as LicensePayload;
}

function validateLegacyCode(code: string, installId: string): LicenseStatus {
  if (!LICENSE_PUBLIC_KEY.trim()) throw new Error("当前版本尚未配置许可证公钥");
  const [encodedPayload, encodedSignature, ...extra] = code.trim().split(".");
  if (!encodedPayload || !encodedSignature || extra.length) throw new Error("授权码格式无效");

  const verifier = createVerify("RSA-SHA256");
  verifier.update(encodedPayload);
  verifier.end();
  if (!verifier.verify(LICENSE_PUBLIC_KEY, Buffer.from(encodedSignature, "base64url"))) {
    throw new Error("授权码签名无效");
  }

  const payload = decodePayload(encodedPayload);
  if (payload.installId !== installId) throw new Error("授权码不属于当前安装，请确认安装 ID");
  if (payload.expiresAt && expiryTimestamp(payload.expiresAt) < Date.now()) throw new Error("专业版授权已到期");
  return statusForPro(installId, payload.maxConcurrentRunners ?? 3, payload.expiresAt);
}

function compactCodeBytes(code: string) {
  const normalized = code.trim().replace(/[\s-]/g, "");
  if (!normalized.startsWith(COMPACT_PREFIX)) throw new Error("授权码格式无效");
  const encoded = normalized.slice(COMPACT_PREFIX.length).replaceAll("~", "-");
  if (!encoded) throw new Error("授权码格式无效");
  const bytes = Buffer.from(encoded, "base64url");
  if (bytes.length !== COMPACT_PAYLOAD_SIZE + COMPACT_SIGNATURE_SIZE) throw new Error("授权码长度无效");
  return bytes;
}

function validateCompactCode(code: string, installId: string): LicenseStatus {
  if (!LICENSE_ED25519_PUBLIC_KEY.trim()) throw new Error("当前版本尚未配置紧凑许可证公钥");
  const bytes = compactCodeBytes(code);
  const payload = bytes.subarray(0, COMPACT_PAYLOAD_SIZE);
  const signature = bytes.subarray(COMPACT_PAYLOAD_SIZE);
  if (!verify(null, payload, LICENSE_ED25519_PUBLIC_KEY, signature)) throw new Error("授权码签名无效");
  if (payload[0] !== 2 || (payload[1] !== 0 && payload[1] !== 1)) throw new Error("授权码版本无效");
  if (!payload.subarray(2, 18).equals(installationIdBytes(installId))) {
    throw new Error("授权码不属于当前安装，请确认安装 ID");
  }
  const expires = payload[1] === 1;
  const expiryDay = payload.readUInt32BE(18);
  if ((!expires && expiryDay !== 0) || (expires && expiryDay === 0)) throw new Error("授权码到期信息无效");
  const maxConcurrentRunners = payload[22];
  if (maxConcurrentRunners < 2 || maxConcurrentRunners > 10) throw new Error("授权码并发配额无效");
  const expiresAt = expires ? isoDateFromDay(expiryDay) : undefined;
  if (expiresAt && expiryTimestamp(expiresAt) < Date.now()) throw new Error("专业版授权已到期");
  return statusForPro(installId, maxConcurrentRunners, expiresAt);
}

function validateCode(code: string, installId: string): LicenseStatus {
  return code.trim().replace(/[\s-]/g, "").startsWith(COMPACT_PREFIX)
    ? validateCompactCode(code, installId)
    : validateLegacyCode(code, installId);
}

export function getLicenseStatus(): LicenseStatus {
  const store = readStore();
  if (!store.code) return freeStatus(store.installId, "免费版：可同时运行 1 个任务");
  try {
    return validateCode(store.code, store.installId);
  } catch (error) {
    return freeStatus(store.installId, `授权不可用：${String(error instanceof Error ? error.message : error)}`);
  }
}

export function activateLicense(code: string): LicenseStatus {
  const store = readStore();
  const status = validateCode(code, store.installId);
  writeStore({ ...store, code: code.trim() });
  return status;
}

export function clearLicense(): LicenseStatus {
  const store = readStore();
  writeStore({ installId: store.installId });
  return getLicenseStatus();
}
