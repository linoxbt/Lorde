-- Console support (spec Section 13.1). The Console has no service-role key: it reads and writes with the
-- signed-in user's session, so everything here either runs as the caller under RLS (SECURITY INVOKER) or,
-- for data deletion, checks the caller's role itself (SECURITY DEFINER, owner only).
-- Does not depend on 0005; only wraps expired_objects() so owner-requested deletions reach the retention purge.

-- Aggregates the Console used to compute in the browser from capped row reads ------------------------------

-- Usage per UTC day for the last `days` days (today included), every day present, zero when idle.
create or replace function public.usage_daily(o uuid, days int default 14)
  returns table (day date, judgments bigint, calls bigint, prompt_tokens bigint, completion_tokens bigint)
  language sql stable security invoker set search_path = public, pg_temp as
$$
  with span as (select least(greatest(coalesce(days, 14), 1), 366) as n, (now() at time zone 'utc')::date as today),
  agg as (
    select (u.created_at at time zone 'utc')::date as day, coalesce(sum(u.judgments), 0) as judgments, count(*) as calls,
           coalesce(sum(u.prompt_tokens), 0) as prompt_tokens, coalesce(sum(u.completion_tokens), 0) as completion_tokens
      from usage_events u, span
     where u.org_id = o and is_member(o)
       and u.created_at >= ((span.today - (span.n - 1))::timestamp at time zone 'utc')
     group by 1)
  select d::date, coalesce(a.judgments, 0)::bigint, coalesce(a.calls, 0)::bigint,
         coalesce(a.prompt_tokens, 0)::bigint, coalesce(a.completion_tokens, 0)::bigint
    from span, generate_series(span.today - (span.n - 1), span.today, interval '1 day') d
    left join agg a on a.day = d::date
   order by 1
$$;

-- Pass rate per gate over the judgments of jobs created in the last `days` days.
create or replace function public.gate_pass_rates(o uuid, days int default 30)
  returns table (gate gate_kind, judged bigint, passed bigint)
  language sql stable security invoker set search_path = public, pg_temp as
$$
  select j.gate, count(*) filter (where j.decision is not null), count(*) filter (where j.decision)
    from judgments j join jobs b on b.id = j.job_id
   where b.org_id = o and is_member(o)
     and b.created_at > now() - make_interval(days => least(greatest(coalesce(days, 30), 1), 366))
   group by j.gate
$$;

-- Score histogram of one job: counts per gate, language and 0.1-wide score bin (bin is null when unscored).
create or replace function public.job_judgment_stats(job uuid)
  returns table (gate gate_kind, lang text, bin int, passed bigint, rejected bigint, undecided bigint)
  language sql stable security invoker set search_path = public, pg_temp as
$$
  select j.gate, coalesce(j.lang, '?'),
         case when j.score is null then null else least(9, greatest(0, floor(round(j.score::numeric, 4) * 10)::int)) end,
         count(*) filter (where j.decision), count(*) filter (where not j.decision), count(*) filter (where j.decision is null)
    from judgments j join jobs b on b.id = j.job_id
   where j.job_id = job and is_member(b.org_id)
   group by 1, 2, 3
$$;

-- Review: judged rows the caller has not labelled yet. Only jobs run while the org stored content have
-- `parsed`; newest jobs first.
create or replace function public.review_queue(o uuid, n int default 50)
  returns table (id bigint, job_id uuid, row_idx int, gate gate_kind, lang text, score real, decision boolean,
                 verdict text, parsed jsonb)
  language sql stable security invoker set search_path = public, pg_temp as
$$
  select j.id, j.job_id, j.row_idx, j.gate, j.lang, j.score, j.decision, j.verdict, j.parsed
    from judgments j join jobs b on b.id = j.job_id
   where b.org_id = o and is_member(o) and j.parsed is not null
     and not exists (select 1 from human_labels h
                      where h.org_id = o and h.labeler = auth.uid() and h.item_ref = 'judgment:' || j.id)
   order by b.created_at desc, j.row_idx, j.gate
   limit least(greatest(coalesce(n, 50), 1), 200)
$$;

-- The caller's latest label per judged row, with Lorde's score, so agreement survives a reload.
create or replace function public.my_review_labels(o uuid, n int default 500)
  returns table (judgment_id bigint, kind gate_kind, human_score real, lorde_score real, labeled_at timestamptz)
  language sql stable security invoker set search_path = public, pg_temp as
$$
  select l.jid, l.kind, l.score, j.score, l.created_at
    from (select distinct on (h.item_ref) substr(h.item_ref, 10)::bigint as jid, h.kind, h.score, h.created_at
            from human_labels h
           where h.org_id = o and is_member(o) and h.labeler = auth.uid() and h.item_ref ~ '^judgment:[0-9]+$'
           order by h.item_ref, h.created_at desc, h.id desc) l
    join judgments j on j.id = l.jid
   order by l.created_at desc
   limit least(greatest(coalesce(n, 500), 1), 2000)
$$;

-- Members with their email (auth.users is not readable by users). Members of `o` only.
create or replace function public.org_members_with_email(o uuid)
  returns table (user_id uuid, email text, role org_role, created_at timestamptz)
  language sql stable security definer set search_path = public, pg_temp as
$$
  select m.user_id, u.email::text, m.role, m.created_at
    from org_members m join auth.users u on u.id = m.user_id
   where m.org_id = o and is_member(o)
   order by m.created_at, m.user_id
$$;

-- Owner-requested deletion of an organization's content (spec NFR-06) ----------------------------------------

-- Job result files are removed by the worker's retention purge (expired_objects -> storage delete ->
-- finish_purge). A deletion request marks them expired now.
alter table public.jobs add column if not exists results_expire_at timestamptz;

do $$ begin
  if not exists (select 1 from pg_proc p join pg_namespace s on s.oid = p.pronamespace
                  where s.nspname = 'public' and p.proname = 'expired_objects_by_retention') then
    alter function public.expired_objects() rename to expired_objects_by_retention;
  end if;
end $$;

create or replace function public.expired_objects() returns table (kind text, id uuid, org_id uuid, path text)
  language sql stable set search_path = public, pg_temp as
$$
  select * from public.expired_objects_by_retention()
  union
  select 'result', j.id, j.org_id, p.value #>> '{}' from jobs j, jsonb_each(j.results) p
   where j.results_expire_at is not null and j.results_expire_at <= now()
$$;

revoke execute on function public.expired_objects(), public.expired_objects_by_retention() from public, anon, authenticated;

-- Deletes judgments and human labels now, cancels queued and running jobs, and expires every dataset and
-- job result file so the next retention purge (every 6 hours) deletes the files and the dataset rows.
-- Usage events (billing), API keys, members and the audit log are kept. Owners only.
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
  update jobs set status = 'cancelled', cancel_requested = true, finished_at = now(), locked_by = null
   where org_id = o and status = 'queued';
  get diagnostics n_cancelled = row_count;
  update jobs set cancel_requested = true where org_id = o and status = 'running' and not cancel_requested;
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

-- Grants: signed-in users only (RLS and the checks above decide what they see or change).
revoke execute on function public.usage_daily(uuid, int), public.gate_pass_rates(uuid, int),
  public.job_judgment_stats(uuid), public.review_queue(uuid, int), public.my_review_labels(uuid, int),
  public.org_members_with_email(uuid), public.request_org_data_deletion(uuid) from public, anon;
grant execute on function public.usage_daily(uuid, int), public.gate_pass_rates(uuid, int),
  public.job_judgment_stats(uuid), public.review_queue(uuid, int), public.my_review_labels(uuid, int),
  public.org_members_with_email(uuid), public.request_org_data_deletion(uuid) to authenticated;
