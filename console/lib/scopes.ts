/** API key scopes (gateway/app.py enforces them on every route) and what the Console's screens need. */
export const SCOPES = ["judge", "jobs:read", "jobs:write", "usage:read"] as const;
export type Scope = (typeof SCOPES)[number];
export const SCOPE_LABELS: Record<Scope, string> = {
  judge: "Judge items", "jobs:read": "Read jobs and download results", "jobs:write": "Upload datasets, create and cancel jobs",
  "usage:read": "Read usage",
};
/** New keys get every scope by default, so the Datasets, Jobs and Playground screens all work with them. */
export const DEFAULT_SCOPES: Scope[] = [...SCOPES];
/** Per-key rate limit cap; the database enforces the same ceiling with a CHECK on api_keys.rpm. */
export const MAX_RPM = 6000;
