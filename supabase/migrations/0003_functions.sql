-- Job queue, retention and onboarding functions (spec Sections 11.4, 13.2, 14.3).

-- An org with its owner is created on a user's first sign-in.
create or replace function public.handle_new_user() returns trigger
  language plpgsql security definer set search_path = public, pg_temp as
$$
declare o uuid;
begin
  insert into orgs (name) values (coalesce(split_part(new.email, '@', 1), 'my') || '''s org') returning id into o;
  insert into org_members (org_id, user_id, role) values (o, new.id, 'owner');
  return new;
end $$;

create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- Claim the oldest queued job whose org has fewer than max_running jobs running (SKIP LOCKED queue).
-- A per-org advisory lock, held until commit, serializes the running-count check so concurrent
-- workers cannot push an org past its cap. It is taken with try-lock (never waits, so no deadlocks):
-- an org another worker is deciding for, or one already at its cap, is skipped for other orgs.
create or replace function public.claim_job(worker text, max_running int default 3) returns setof jobs
  language plpgsql as
$$
declare
  cand  record;
  skip_orgs uuid[] := '{}';
  n_running int;
begin
  loop
    select q.id, q.org_id into cand from jobs q
     where q.status = 'queued' and not q.cancel_requested and not (q.org_id = any(skip_orgs))
     order by q.created_at
     for update skip locked
     limit 1;
    if not found then
      return;
    end if;
    -- Two statements on purpose: in one boolean expression Postgres may run the count (an uncorrelated
    -- subquery, i.e. an InitPlan) before the lock is taken, which reopens the race.
    if pg_try_advisory_xact_lock(hashtextextended(cand.org_id::text, 0)) then
      select count(*) into n_running from jobs r where r.org_id = cand.org_id and r.status = 'running';
    else
      n_running := max_running;  -- another worker is deciding for this org right now
    end if;
    if n_running < max_running then
      return query
      update jobs j set status = 'running', locked_by = worker, heartbeat_at = now(),
                        started_at = coalesce(j.started_at, now()), attempts = j.attempts + 1
       where j.id = cand.id
      returning j.*;
      return;
    end if;
    skip_orgs := skip_orgs || cand.org_id;
  end loop;
end $$;

-- Jobs whose worker stopped heartbeating for 2 minutes go back to the queue, at most twice.
create or replace function public.requeue_stale_jobs(stale interval default interval '2 minutes',
                                                     max_attempts int default 3) returns int
  language plpgsql as
$$
declare n int;
begin
  with stale_jobs as (
    select id, attempts from jobs
    where status = 'running' and heartbeat_at < now() - stale
    for update skip locked)
  update jobs j set
    status = case when s.attempts >= max_attempts then 'failed'::job_status else 'queued'::job_status end,
    error = case when s.attempts >= max_attempts then 'worker heartbeat lost; gave up after '
                 || s.attempts || ' attempts' else j.error end,
    finished_at = case when s.attempts >= max_attempts then now() else null end,
    locked_by = null
  from stale_jobs s where j.id = s.id;
  get diagnostics n = row_count;
  return n;
end $$;

-- Datasets and job outputs past their org's retention. Returns the storage paths to delete;
-- the caller deletes the objects, then calls finish_purge() to delete rows and audit it.
create or replace function public.expired_objects() returns table (kind text, id uuid, org_id uuid, path text)
  language sql stable as
$$
  select 'dataset', d.id, d.org_id, d.storage_path from datasets d
   where coalesce(d.expires_at, d.created_at + make_interval(days => (select retention_days from orgs o where o.id = d.org_id))) < now()
  union all
  select 'result', j.id, j.org_id, p.value #>> '{}' from jobs j, jsonb_each(j.results) p
   join orgs o on true
   where o.id = j.org_id and j.finished_at < now() - make_interval(days => o.retention_days)
$$;

create or replace function public.finish_purge(dataset_ids uuid[], job_ids uuid[]) returns void
  language plpgsql as
$$
begin
  insert into audit_log (org_id, actor, action, target, detail)
    select org_id, 'system', 'data.delete', 'dataset:' || id, jsonb_build_object('reason', 'retention')
    from datasets where id = any(dataset_ids);
  delete from datasets where id = any(dataset_ids);
  insert into audit_log (org_id, actor, action, target, detail)
    select org_id, 'system', 'data.delete', 'job_results:' || id, jsonb_build_object('reason', 'retention')
    from jobs where id = any(job_ids);
  update jobs set results = '{}' where id = any(job_ids);
end $$;

revoke execute on function public.claim_job(text, int), public.requeue_stale_jobs(interval, int),
  public.expired_objects(), public.finish_purge(uuid[], uuid[]) from public, anon, authenticated;
