-- Hardening after the 11 Nov 2026 audit (S1, S2, S3, S8, S13, B8, S4, M5). Applied once, in one transaction.
-- 0001-0004 are already applied in production and stay unchanged; everything here is additive or a
-- CREATE OR REPLACE of a function, and safe to re-read: each step says what it guards against.

-- --------------------------------------------------------------------------------------------- S1
-- datasets: only the Gateway (service credentials) writes rows. A user-written storage_path let anyone
-- point a job or the retention purge at another org's objects, or crash the worker with '../'.
drop policy if exists datasets_insert on datasets;
revoke insert, update on datasets from authenticated, anon;

-- The only shape the Gateway writes (gateway/db.py JobStore.create_dataset): {org_id}/{id}/{name}, where
-- name is gateway/jobs.py safe_name(): [A-Za-z0-9._-], no leading dot, at most 120 characters.
-- NOT VALID first so old rows can never block this migration; validated right away when they all comply.
alter table datasets add constraint datasets_storage_path_shape check (
  name is not null
  and name ~ '^[A-Za-z0-9_-][A-Za-z0-9._-]{0,199}$'
  and storage_path = org_id::text || '/' || id::text || '/' || name
) not valid;
do $$ begin
  if not exists (select 1 from datasets where not (name is not null
                 and name ~ '^[A-Za-z0-9_-][A-Za-z0-9._-]{0,199}$'
                 and storage_path = org_id::text || '/' || id::text || '/' || name)) then
    alter table datasets validate constraint datasets_storage_path_shape;
  else
    raise warning 'datasets_storage_path_shape left NOT VALID: some existing rows do not match';
  end if;
end $$;

-- --------------------------------------------------------------------------------------------- S2
-- api_keys: users may insert keys (checked below) and change only the name and revoked_at; rpm,
-- scopes, prefix, hash and org are fixed once created. Revocation is one-way.
revoke insert, update on api_keys from authenticated, anon;
grant insert (org_id, name, prefix, key_hash, scopes, rpm, created_by) on api_keys to authenticated;
grant update (name, revoked_at) on api_keys to authenticated;

-- 6000 judgments/minute: 10x the default of 600 and well past what one Serve replica sustains.
update api_keys set rpm = 6000 where rpm > 6000;
alter table api_keys add constraint api_keys_rpm_cap check (rpm between 1 and 6000);
alter table api_keys add constraint api_keys_scopes_known check (
  cardinality(scopes) between 1 and 4
  and scopes <@ array['judge', 'jobs:read', 'jobs:write', 'usage:read']::text[]
) not valid;
do $$ begin
  if not exists (select 1 from api_keys where not (cardinality(scopes) between 1 and 4
                 and scopes <@ array['judge', 'jobs:read', 'jobs:write', 'usage:read']::text[])) then
    alter table api_keys validate constraint api_keys_scopes_known;
  else
    raise warning 'api_keys_scopes_known left NOT VALID: some existing keys carry unknown scopes';
  end if;
end $$;
alter table api_keys add constraint api_keys_prefix_shape check (prefix ~ '^lorde_sk_[A-Za-z0-9]{8}$') not valid;

create or replace function public.api_keys_guard() returns trigger
  language plpgsql security definer set search_path = public, pg_temp as
$$
begin
  if tg_op = 'INSERT' then
    if auth.uid() is not null then
      new.created_by := auth.uid();       -- a signed-in user cannot attribute a key to someone else
    end if;
    if new.revoked_at is not null and auth.uid() is not null then
      raise exception 'a new key cannot be created revoked' using errcode = '42501';
    end if;
    return new;
  end if;
  -- UPDATE: revocation is one-way and final, for every role (a leaked key must stay dead).
  if old.revoked_at is not null and new.revoked_at is distinct from old.revoked_at then
    raise exception 'api key % is revoked; revocation cannot be undone or changed', old.prefix
      using errcode = '42501';
  end if;
  if new.org_id is distinct from old.org_id or new.prefix is distinct from old.prefix
     or new.key_hash is distinct from old.key_hash then
    raise exception 'org_id, prefix and key_hash of an api key are immutable' using errcode = '42501';
  end if;
  return new;
end $$;

drop trigger if exists api_keys_guard on api_keys;
create trigger api_keys_guard before insert or update on api_keys
  for each row execute function public.api_keys_guard();

-- --------------------------------------------------------------------------------------------- S3
-- org_members: owners manage owners and admins; admins manage plain members only; anyone may leave;
-- nobody can remove or demote an org's last owner.
create or replace function public.is_owner(o uuid) returns boolean
  language sql stable security definer set search_path = public, pg_temp as
$$ select exists (select 1 from org_members where org_id = o and user_id = auth.uid() and role = 'owner') $$;

drop policy if exists members_write on org_members;
create policy members_insert on org_members for insert to authenticated
  with check (is_owner(org_id) or (is_admin(org_id) and role = 'member'));
create policy members_update on org_members for update to authenticated
  using (is_owner(org_id) or (is_admin(org_id) and role = 'member'))
  with check (is_owner(org_id) or (is_admin(org_id) and role = 'member'));
create policy members_delete on org_members for delete to authenticated
  using (is_owner(org_id) or (is_admin(org_id) and role = 'member') or user_id = auth.uid());

revoke insert, update, delete on org_members from authenticated, anon;
grant insert (org_id, user_id, role) on org_members to authenticated;
grant update (role) on org_members to authenticated;
grant delete on org_members to authenticated;

create or replace function public.org_members_last_owner() returns trigger
  language plpgsql security definer set search_path = public, pg_temp as
$$
begin
  if old.role <> 'owner' then
    return coalesce(new, old);
  end if;
  if tg_op = 'UPDATE' and new.role = 'owner' and new.org_id = old.org_id then
    return new;
  end if;
  -- cascades from deleting the org or the user account are not blocked
  if not exists (select 1 from orgs where id = old.org_id)
     or not exists (select 1 from auth.users where id = old.user_id) then
    return coalesce(new, old);
  end if;
  perform 1 from org_members where org_id = old.org_id and role = 'owner' and user_id <> old.user_id for update;
  if not found then
    raise exception 'an organization must keep at least one owner' using errcode = '42501';
  end if;
  return coalesce(new, old);
end $$;

drop trigger if exists org_members_last_owner on org_members;
create trigger org_members_last_owner before update or delete on org_members
  for each row execute function public.org_members_last_owner();

-- --------------------------------------------------------------------------------------------- S8
-- Create the user's org only once their email is confirmed (OAuth users arrive confirmed), so
-- unconfirmed bot sign-ups no longer create orgs. Idempotent: a user with any membership is skipped.
do $$ begin  -- the test shim's auth.users may predate this column; Supabase always has it
  if not exists (select 1 from information_schema.columns
                 where table_schema = 'auth' and table_name = 'users' and column_name = 'email_confirmed_at') then
    alter table auth.users add column email_confirmed_at timestamptz;
  end if;
end $$;

create or replace function public.handle_new_user() returns trigger
  language plpgsql security definer set search_path = public, pg_temp as
$$
declare o uuid;
begin
  if new.email_confirmed_at is null then
    return new;
  end if;
  perform pg_advisory_xact_lock(hashtextextended('lorde.onboard:' || new.id::text, 0));
  if exists (select 1 from org_members where user_id = new.id) then
    return new;
  end if;
  insert into orgs (name) values (coalesce(split_part(new.email, '@', 1), 'my') || '''s org') returning id into o;
  insert into org_members (org_id, user_id, role) values (o, new.id, 'owner');
  return new;
end $$;

-- 0003's on_auth_user_created (AFTER INSERT) stays as it is: the function above now returns early for
-- unconfirmed users. Only a new trigger is added (DROP TRIGGER on auth.users needs table ownership,
-- which the migration role may not have on Supabase; CREATE TRIGGER needs only the TRIGGER privilege).
do $$ begin
  if not exists (select 1 from pg_trigger where tgname = 'on_auth_user_confirmed'
                 and tgrelid = 'auth.users'::regclass) then
    create trigger on_auth_user_confirmed after update of email_confirmed_at on auth.users
      for each row when (old.email_confirmed_at is null and new.email_confirmed_at is not null)
      execute function public.handle_new_user();
  end if;
end $$;

-- --------------------------------------------------------------------------------------------- B8
-- One judgment per (job, row, gate): a retried or crashed job cannot duplicate rows.
delete from judgments a using judgments b
 where a.job_id = b.job_id and a.row_idx = b.row_idx and a.gate = b.gate and a.id > b.id;
create unique index if not exists judgments_job_row_gate on judgments (job_id, row_idx, gate);
drop index if exists judgments_job;   -- covered by the unique index's leading columns

-- Stale jobs whose cancel was requested become cancelled; the rest are re-queued at most twice.
create or replace function public.requeue_stale_jobs(stale interval default interval '2 minutes',
                                                     max_attempts int default 3) returns int
  language plpgsql set search_path = public, pg_temp as
$$
declare n int;
begin
  with stale_jobs as (
    select id, attempts, cancel_requested from jobs
    where status = 'running' and heartbeat_at < now() - stale
    for update skip locked)
  update jobs j set
    status = case when s.cancel_requested then 'cancelled'::job_status
                  when s.attempts >= max_attempts then 'failed'::job_status
                  else 'queued'::job_status end,
    error = case when s.cancel_requested then j.error
                 when s.attempts >= max_attempts then 'worker heartbeat lost; gave up after '
                 || s.attempts || ' attempts' else j.error end,
    finished_at = case when s.cancel_requested or s.attempts >= max_attempts then now() else null end,
    locked_by = null
  from stale_jobs s where j.id = s.id;
  get diagnostics n = row_count;
  return n;
end $$;

-- Jobs already stuck as queued + cancel_requested (claim_job skips them forever) are cancelled now.
update jobs set status = 'cancelled', finished_at = coalesce(finished_at, now())
 where status = 'queued' and cancel_requested;

-- POST /v1/jobs with an Idempotency-Key returns the job a previous identical request created.
alter table jobs add column if not exists idempotency_key text;
create unique index if not exists jobs_idempotency on jobs (org_id, idempotency_key)
  where idempotency_key is not null;

-- ------------------------------------------------------------------------------------------ S4/M7
-- usage_events.source: 'api' (REST), 'mcp' or 'job' (judgments made by the job worker).
alter table usage_events add column if not exists source text not null default 'api';

-- --------------------------------------------------------------------------------------------- S13
-- Pinned search_path on the queue and retention functions; EXECUTE only where policies need it.
alter function public.claim_job(text, int) set search_path = public, pg_temp;
alter function public.expired_objects() set search_path = public, pg_temp;
alter function public.finish_purge(uuid[], uuid[]) set search_path = public, pg_temp;

revoke execute on function public.is_member(uuid), public.is_admin(uuid), public.is_owner(uuid)
  from public, anon;
grant execute on function public.is_member(uuid), public.is_admin(uuid), public.is_owner(uuid) to authenticated;
-- trigger functions fire without EXECUTE; nobody needs to call them directly
revoke execute on function public.handle_new_user(), public.audit_api_keys(), public.api_keys_guard(),
  public.org_members_last_owner() from public, anon, authenticated;
revoke execute on function public.claim_job(text, int), public.requeue_stale_jobs(interval, int),
  public.expired_objects(), public.finish_purge(uuid[], uuid[]) from public, anon, authenticated;

-- ------------------------------------------------------------------------------------------- perf
-- Foreign keys the Supabase advisor flagged as unindexed.
create index if not exists jobs_dataset on jobs (dataset_id) where dataset_id is not null;
create index if not exists jobs_api_key on jobs (api_key_id) where api_key_id is not null;
create index if not exists api_keys_created_by on api_keys (created_by) where created_by is not null;
create index if not exists datasets_created_by on datasets (created_by) where created_by is not null;
create index if not exists human_labels_labeler on human_labels (labeler) where labeler is not null;
