import assert from "node:assert/strict";
import { test } from "node:test";

import { datasetState, jobDatasetLabel, readPending, UPLOAD_WINDOW_MS, writePending } from "../../lib/datasets.ts";

const NOW = Date.parse("2026-11-12T12:00:00Z");

test("expired and unfinished uploads are not ready", () => {
  assert.equal(datasetState({ expires_at: null }, NOW), "ready");
  assert.equal(datasetState({ expires_at: "2026-12-01T00:00:00Z" }, NOW), "ready");
  assert.equal(datasetState({ expires_at: "2026-11-12T11:59:59Z" }, NOW), "expired");
  assert.equal(datasetState({ expires_at: null }, NOW, NOW - 60_000), "uploading");
  assert.equal(datasetState({ expires_at: null }, NOW, NOW - UPLOAD_WINDOW_MS), "incomplete");
  assert.equal(datasetState({ expires_at: "2026-11-01T00:00:00Z" }, NOW, NOW), "expired");
});

test("a job's dataset label never claims a Hub dataset for a deleted upload", () => {
  const cfg = (source?: object) => ({ gates: { grounding: 0.7, reward: null }, options: { pii: false, dedup: "exact" }, source });
  assert.equal(jobDatasetLabel({ dataset_name: "train.jsonl", config: cfg() }), "train.jsonl");
  assert.equal(jobDatasetLabel({ config: cfg({ dataset_url: "hf://datasets/acme/chat/train.jsonl" }) }), "acme/chat/train.jsonl");
  assert.equal(jobDatasetLabel({ config: cfg({ storage_path: "datasets/o1/d1/support.csv", format: "csv" }) }), "support.csv (deleted)");
  assert.equal(jobDatasetLabel({ config: cfg() }), "Deleted dataset");
});

test("pending uploads survive junk in localStorage", () => {
  const m = new Map<string, string>();
  const store = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) };
  assert.deepEqual(readPending(store), {});
  writePending(store, { a: NOW, old: NOW - 8 * 24 * 3600 * 1000 }, NOW);
  assert.deepEqual(readPending(store), { a: NOW });
  m.set("lorde.pendingUploads", "not json");
  assert.deepEqual(readPending(store), {});
  m.set("lorde.pendingUploads", JSON.stringify({ a: "x", b: 5 }));
  assert.deepEqual(readPending(store), { b: 5 });
  assert.deepEqual(readPending(undefined), {});
});
