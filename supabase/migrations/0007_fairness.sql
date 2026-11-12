-- Tenant fairness and follow-ups after the second audit (N1c, N6, N8, N9). Applied once, in one transaction.
-- 0001-0006 are applied in production (0006 before 0005): everything here is a CREATE OR REPLACE, an ALTER or
-- an IF [NOT] EXISTS step, correct after either order.

-- --------------------------------------------------------------------------------------------- N1c
-- Round-robin across organizations: the next job comes from the org whose latest job started longest ago
-- (an org with no started job first), then oldest first within the org. Unchanged from 0003: at most
-- `max_running` running jobs per org, SKIP LOCKED, and the per-org advisory try-lock around the count.
create index if not exists jobs_org_started on jobs (org_id, started_at desc nulls last);

create or replace function public.claim_job(worker text, max_running int default 3) returns setof jobs
  language plpgsql set search_path = public, pg_temp as
$$
declare
  cand  record;
  skip_orgs uuid[] := '{}';
  n_running int;
begin
  loop
    select q.id, q.org_id into cand from jobs q
     where q.status = 'queued' and not q.cancel_requested and not (q.org_id = any(skip_orgs))
     order by (select max(r.started_at) from jobs r where r.org_id = q.org_id) asc nulls first, q.created_at, q.id
     for update of q skip locked
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

revoke execute on function public.claim_job(text, int) from public, anon, authenticated;

-- ---------------------------------------------------------------------------------------------- N6
-- With 0006 applied before 0005, 0005 pinned the search_path of 0006's expired_objects() wrapper but not of
-- the renamed original, expired_objects_by_retention().
do $$ begin
  if exists (select 1 from pg_proc p join pg_namespace s on s.oid = p.pronamespace
              where s.nspname = 'public' and p.proname = 'expired_objects_by_retention' and p.pronargs = 0) then
    alter function public.expired_objects_by_retention() set search_path = public, pg_temp;
    revoke execute on function public.expired_objects_by_retention() from public, anon, authenticated;
  end if;
end $$;

-- ---------------------------------------------------------------------------------------------- N9
-- The review queue offers only judgments that have a score to compare a human label with.
create or replace function public.review_queue(o uuid, n int default 50)
  returns table (id bigint, job_id uuid, row_idx int, gate gate_kind, lang text, score real, decision boolean,
                 verdict text, parsed jsonb)
  language sql stable security invoker set search_path = public, pg_temp as
$$
  select j.id, j.job_id, j.row_idx, j.gate, j.lang, j.score, j.decision, j.verdict, j.parsed
    from judgments j join jobs b on b.id = j.job_id
   where b.org_id = o and is_member(o) and j.parsed is not null and j.score is not null
     and not exists (select 1 from human_labels h
                      where h.org_id = o and h.labeler = auth.uid() and h.item_ref = 'judgment:' || j.id)
   order by b.created_at desc, j.row_idx, j.gate
   limit least(greatest(coalesce(n, 50), 1), 200)
$$;

revoke execute on function public.review_queue(uuid, int) from public, anon;
grant execute on function public.review_queue(uuid, int) to authenticated;

-- ---------------------------------------------------------------------------------------------- N8
-- 0005 added these checks NOT VALID (old rows could never block it). Validate each one that every row now
-- satisfies; otherwise leave it NOT VALID (still enforced on new and changed rows) and say so.
do $$
declare c record;
begin
  for c in select t.relname as tbl, k.conname from pg_constraint k join pg_class t on t.oid = k.conrelid
            where k.connamespace = 'public'::regnamespace and k.contype = 'c' and not k.convalidated
              and k.conname in ('api_keys_prefix_shape', 'api_keys_scopes_known', 'datasets_storage_path_shape')
  loop
    begin
      execute format('alter table public.%I validate constraint %I', c.tbl, c.conname);
    exception when check_violation then
      raise notice '% left NOT VALID: some existing % rows do not satisfy it', c.conname, c.tbl;
    end;
  end loop;
end $$;
