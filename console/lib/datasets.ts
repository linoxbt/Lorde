/**
 * Dataset and job-source labels (pure: unit-tested in tests/unit/datasets.test.ts).
 *
 * The database has no "upload finished" flag: the Gateway creates the dataset row when an upload starts. So the
 * Console marks what it can know: rows past `expires_at` (waiting for the retention purge, file gone or about to
 * be), and uploads this browser started whose file transfer did not finish (kept in localStorage by
 * datasets-view.tsx). Neither can start a job.
 */
import type { Dataset, Job } from "./types";

export type DatasetState = "ready" | "uploading" | "incomplete" | "expired";

/** How long a started upload may still be in flight (the Gateway's signed upload URLs last at most two hours). */
export const UPLOAD_WINDOW_MS = 2 * 3600 * 1000;

/**
 * `pending` is this browser's record for the dataset (see readPending): the time its upload started, or minus the
 * time it failed. No record: nothing is known against it.
 */
export function datasetState(d: Pick<Dataset, "expires_at">, now: number, pending?: number): DatasetState {
  if (d.expires_at && Date.parse(d.expires_at) <= now) return "expired";
  if (pending !== undefined) return pending > 0 && now - pending < UPLOAD_WINDOW_MS ? "uploading" : "incomplete";
  return "ready";
}

export const DATASET_STATE_LABEL: Record<DatasetState, string> = {
  ready: "Ready", uploading: "Uploading", incomplete: "Upload incomplete", expired: "Expired",
};

/** The dataset a job ran on: its name, the Hub dataset it named, or the file of an upload since deleted. */
export function jobDatasetLabel(j: Pick<Job, "dataset_name" | "config">): string {
  if (j.dataset_name) return j.dataset_name;
  const src = j.config?.source;
  if (src?.dataset_url) return src.dataset_url.replace(/^hf:\/\/datasets\//, "");
  if (src?.storage_path) return `${src.storage_path.split("/").pop() || "Upload"} (deleted)`;
  return "Deleted dataset";
}

/**
 * Uploads this browser started and has not seen finish: { dataset id: started at (ms) }, or minus the time the file
 * transfer failed. Finished uploads are removed.
 */
const PENDING_KEY = "lorde.pendingUploads";
export function readPending(store: Pick<Storage, "getItem"> | undefined): Record<string, number> {
  try {
    const v = JSON.parse(store?.getItem(PENDING_KEY) ?? "{}") as unknown;
    if (!v || typeof v !== "object" || Array.isArray(v)) return {};
    return Object.fromEntries(Object.entries(v).filter(([, t]) => typeof t === "number")) as Record<string, number>;
  } catch {
    return {};
  }
}
export function writePending(store: Pick<Storage, "setItem"> | undefined, pending: Record<string, number>, now = Date.now()) {
  // forget entries older than a week: the dataset row is purged by then anyway
  const kept = Object.fromEntries(Object.entries(pending).filter(([, t]) => now - Math.abs(t) < 7 * 24 * 3600 * 1000));
  try { store?.setItem(PENDING_KEY, JSON.stringify(kept)); } catch { /* storage full or blocked: labels only */ }
}
