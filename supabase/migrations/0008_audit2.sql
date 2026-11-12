-- Fixes after the second full audit (12 Nov 2026): B3/S2, S3, S5. Applied once, in one transaction.
-- 0001-0007 are applied in production (0006 before 0005). Everything here is CREATE OR REPLACE, ALTER ... IF
-- [NOT] EXISTS, DROP ... IF EXISTS or guarded by a catalog check, so it is correct after either order and
-- safe to run again.

-- ------------------------------------------------------------------------------------------- B3/S2
-- Jobs store their judgments while they run, and a cancelled job keeps the judgments it made (they were
-- charged). A data deletion must still leave nothing behind: it now marks queued and running jobs
-- delete_requested, and the worker stores no further judgments for such a job (it checks the flag under the
-- job's row lock before every insert) and uploads no partial results.
alter table public.jobs add column if not exists delete_requested boolean not null default false;

create or replace function public.request_org_data_deletion(o uuid) returns jsonb
  language plpgsql security definer set search_path = public, pg_temp as
$$
declare
  uid uuid := auth.uid();
  n_judgments bigint; n_labels bigint; n_datasets bigint; n_results bigint; n_cancelled bigint; n_stopping bigint;
  result jsonb;
begin
  if uid is null or not exists (select 1 from org_members where org_id = o and user_id = uid and role = 'owner') then
    raise exception 'only an owner of this organization can delete its data' using errcode = '42501';
  end if;
  update jobs set status = 'cancelled', cancel_requested = true, delete_requested = true, finished_at = now(),
                  locked_by = null
   where org_id = o and status = 'queued';
  get diagnostics n_cancelled = row_count;
  update jobs set cancel_requested = true, delete_requested = true
   where org_id = o and status = 'running' and not delete_requested;
  get diagnostics n_stopping = row_count;
  delete from judgments where job_id in (select id from jobs where org_id = o);
  get diagnostics n_judgments = row_count;
  delete from human_labels where org_id = o;
  get diagnostics n_labels = row_count;
  update datasets set expires_at = now() where org_id = o and (expires_at is null or expires_at > now());
  get diagnostics n_datasets = row_count;
  update jobs set results_expire_at = now()
   where org_id = o and results <> '{}'::jsonb and (results_expire_at is null or results_expire_at > now());
  get diagnostics n_results = row_count;
  result := jsonb_build_object('judgments', n_judgments, 'human_labels', n_labels, 'datasets', n_datasets,
                               'job_results', n_results, 'jobs_cancelled', n_cancelled, 'jobs_stopping', n_stopping);
  insert into audit_log (org_id, actor, action, target, detail)
  values (o, uid::text, 'data.delete_request', 'org:' || o, result);
  return result;
end $$;

revoke execute on function public.request_org_data_deletion(uuid) from public, anon;
grant execute on function public.request_org_data_deletion(uuid) to authenticated;

-- ---------------------------------------------------------------------------------------------- S3
-- Storage abuse. 0002's datasets_objects_upload policy let any member write any object under their org's folder
-- in the datasets bucket with their session, with no datasets row (so the retention purge never saw it) and no
-- size or count limit. Objects are now written only through Gateway-issued uploads: a Supabase signed upload URL
-- (created with the service key; it needs no policy) or the Gateway's own /v1/_storage route.
drop policy if exists datasets_objects_upload on storage.objects;

-- The bucket refuses objects over the Gateway's upload limit (Settings.max_upload_bytes, 200 MB), which also bounds
-- a signed upload whose declared size was a lie. (The column exists in Supabase; the test shim mirrors it.)
do $$ begin
  if exists (select 1 from information_schema.columns where table_schema = 'storage' and table_name = 'buckets'
             and column_name = 'file_size_limit') then
    update storage.buckets set file_size_limit = 200 * 1024 * 1024 where id = 'datasets';
  end if;
end $$;

-- When an upload completed (the Gateway's local storage route sets it; for Supabase Storage the object's own
-- metadata says so). Per-org dataset caps count completed uploads at their real size and pending ones (a signed
-- URL that may still be used) at the upload limit.
alter table public.datasets add column if not exists uploaded_at timestamptz;

-- Objects in the datasets bucket with no datasets row, older than a day: written before this migration through
-- the dropped policy, or left behind by a failed purge. The worker deletes them (every 6 hours, with the purge).
create or replace function public.orphan_dataset_objects(max_rows int default 1000) returns table (path text)
  language plpgsql stable set search_path = public, pg_temp as
$$
begin
  return query
  select o.name::text from storage.objects o
   where o.bucket_id = 'datasets' and o.created_at < now() - interval '1 day'
     and not exists (select 1 from public.datasets d where d.storage_path = o.name)
   order by o.created_at
   limit greatest(1, least(coalesce(max_rows, 1000), 10000));
end $$;

revoke execute on function public.orphan_dataset_objects(int) from public, anon, authenticated;
