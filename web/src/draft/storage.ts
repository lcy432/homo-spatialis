// 草稿只存在当前标签页（sessionStorage）：刷新后恢复，关掉标签页就不保留。所有者 2026-10-18 的决定（留存政策定下之前不长期保存）。
import type { ProductMeta } from "../contract/types";

export function safeRead<T>(key: string): T | null {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

export function safeWrite(key: string, value: unknown): boolean {
  try {
    sessionStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

export function safeRemove(key: string): void {
  try {
    sessionStorage.removeItem(key);
  } catch {
    /* 无法访问存储时什么也不做 */
  }
}

/** 存草稿时记下问卷、词表与资料包的版本；版本变了就提示用户重新核对，不静默迁移。 */
export interface StoredVersions {
  schema_version: string;
  questionnaire_version: string;
  vocabulary_version: string;
  health_questionnaire_version: string;
  conversion_version: string;
  data_pack_fingerprint: string;
}

export function versionsOf(meta: ProductMeta): StoredVersions {
  return {
    schema_version: meta.schema_version,
    questionnaire_version: meta.questionnaire_version,
    vocabulary_version: meta.vocabulary_version,
    health_questionnaire_version: meta.health_questionnaire_version,
    conversion_version: meta.conversion_version,
    data_pack_fingerprint: meta.data_pack.content_fingerprint,
  };
}

export function sameVersions(a: StoredVersions, b: StoredVersions): boolean {
  return (Object.keys(a) as (keyof StoredVersions)[]).every((k) => a[k] === b[k]);
}
