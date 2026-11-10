-- Lorde data model (spec Section 13.2). Apply with `supabase db push` or psql.
-- Judged content is never stored by default (NFR-05): judgments hold scores and decisions only,
-- unless the org enables store_content.

create extension if not exists pgcrypto;

create type job_status  as enum ('queued', 'running', 'succeeded', 'failed', 'cancelled');
create type gate_kind   as enum ('reward', 'grounding');
create type org_role    as enum ('owner', 'admin', 'member');
create type model_state as enum ('candidate', 'active', 'retired');

create table orgs (
  id             uuid primary key default gen_random_uuid(),
  name           text not null,
  retention_days int  not null default 30 check (retention_days between 1 and 365),
  store_content  boolean not null default false,       -- debug retention of judged text, off by default
  created_at     timestamptz not null default now()
);

create table org_members (
  org_id     uuid not null references orgs on delete cascade,
  user_id    uuid not null references auth.users on delete cascade,
  role       org_role not null default 'member',
  created_at timestamptz not null default now(),
  primary key (org_id, user_id)
);
create index org_members_user on org_members (user_id);

create table api_keys (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null references orgs on delete cascade,
  name         text not null,
  prefix       text not null unique,                     -- lorde_sk_ + 8 chars, shown in the Console
  key_hash     text not null,                            -- SHA-256(key + server pepper); never readable by users
  scopes       text[] not null default '{judge}',
  rpm          int not null default 600 check (rpm > 0),
  created_by   uuid references auth.users on delete set null,
  created_at   timestamptz not null default now(),
  last_used_at timestamptz,
  revoked_at   timestamptz
);
create index api_keys_org on api_keys (org_id);

create table datasets (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null references orgs on delete cascade,
  name         text,
  storage_path text not null,                            -- datasets/{org_id}/{dataset_id}/{file}
  format       text check (format in ('jsonl', 'csv', 'parquet')),
  rows         int,
  bytes        bigint check (bytes <= 200 * 1024 * 1024),
  sha256       text,
  expires_at   timestamptz,
  created_by   uuid references auth.users on delete set null,
  created_at   timestamptz not null default now()
);
create index datasets_org on datasets (org_id, created_at desc);
create index datasets_expiry on datasets (expires_at) where expires_at is not null;

create table jobs (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid not null references orgs on delete cascade,
  dataset_id     uuid references datasets on delete set null,
  api_key_id     uuid references api_keys on delete set null,
  status         job_status not null default 'queued',
  config         jsonb not null,                         -- gates, thresholds, options
  model_revision text,
  counts         jsonb not null default '{}',            -- progress and pass/reject counts
  results        jsonb not null default '{}',            -- storage paths of passed/rejected/report/provenance
  error          text,
  attempts       int not null default 0,
  locked_by      text,
  heartbeat_at   timestamptz,
  cancel_requested boolean not null default false,
  created_at     timestamptz not null default now(),
  started_at     timestamptz,
  finished_at    timestamptz
);
create index jobs_queue on jobs (status, created_at);
create index jobs_org on jobs (org_id, created_at desc);

create table judgments (
  id         bigint generated always as identity primary key,
  job_id     uuid not null references jobs on delete cascade,
  row_idx    int not null,
  gate       gate_kind not null,
  lang       text,
  score      real,
  decision   boolean,
  verdict    text,
  latency_ms int,
  parsed     jsonb                                       -- only when orgs.store_content
);
create index judgments_job on judgments (job_id, row_idx);

create table human_labels (
  id         bigint generated always as identity primary key,
  org_id     uuid not null references orgs on delete cascade,
  item_ref   text not null,
  kind       gate_kind not null,
  score      real check (score between 0 and 1),
  verdict    text,
  labeler    uuid references auth.users on delete set null,
  note       text,
  created_at timestamptz not null default now()
);
create index human_labels_org on human_labels (org_id, created_at desc);

create table usage_events (
  id                bigint generated always as identity primary key,
  org_id            uuid,
  api_key_id        uuid,
  key_prefix        text,
  route             text,
  judgments         int,
  prompt_tokens     int,
  completion_tokens int,
  latency_ms        int,
  status            int,
  model_revision    text,
  created_at        timestamptz not null default now()
);
create index usage_events_org on usage_events (org_id, created_at desc);

create table model_versions (
  id                    uuid primary key default gen_random_uuid(),
  name                  text,
  hf_repo               text,
  revision              text not null,
  state                 model_state not null default 'candidate',
  bench                 jsonb,
  template_fingerprints jsonb,
  created_at            timestamptz not null default now(),
  promoted_at           timestamptz
);
create unique index model_versions_one_active on model_versions (state) where state = 'active';

create table audit_log (
  id         bigint generated always as identity primary key,
  org_id     uuid references orgs on delete set null,
  actor      text not null,                              -- user id, key prefix or 'system'
  action     text not null,                              -- key.create, key.revoke, job.create, data.delete, ...
  target     text,
  detail     jsonb not null default '{}',
  created_at timestamptz not null default now()
);
create index audit_log_org on audit_log (org_id, created_at desc);
