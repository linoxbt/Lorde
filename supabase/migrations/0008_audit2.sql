-- Fixes after the second full audit (12 Nov 2026): B3/S2, S3, S5, B17. Applied once, in one transaction.
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
-- (Guarded: if the migration role may not alter storage.objects, the rest of 0008 still applies and the warning
-- says to drop the policy in the dashboard: Storage > Policies.)
do $$ begin
  drop policy if exists datasets_objects_upload on storage.objects;
exception when insufficient_privilege then
  raise warning 'could not drop storage policy datasets_objects_upload (%); drop it in the Supabase dashboard', sqlerrm;
end $$;

-- The bucket refuses objects over the Gateway's upload limit (Settings.max_upload_bytes, 200 MB), which also bounds
-- a signed upload whose declared size was a lie. (The column exists in Supabase; the test shim mirrors it.)
do $$ begin
  if exists (select 1 from information_schema.columns where table_schema = 'storage' and table_name = 'buckets'
             and column_name = 'file_size_limit') then
    update storage.buckets set file_size_limit = 200 * 1024 * 1024 where id = 'datasets';
  end if;
exception when insufficient_privilege then
  raise warning 'could not set the datasets bucket file_size_limit (%); set 200 MB in the dashboard', sqlerrm;
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

-- ---------------------------------------------------------------------------------------------- S5
-- Membership needs the member's consent. 0005's members_insert policy let an owner or admin add any user id to
-- their org; adding a user before they confirmed their email also pre-empted their own org (handle_new_user skips
-- users who already have a membership), so their data landed in the inviter's org. Now an owner or admin creates
-- an invitation for an email address, and only a signed-in user with that confirmed email can accept it.
-- Direct inserts into org_members by users are no longer allowed (role changes and removals are unchanged).

create table if not exists public.org_invites (
  id          uuid primary key default gen_random_uuid(),
  org_id      uuid not null references public.orgs on delete cascade,
  email       text not null check (email = lower(email) and length(email) between 3 and 320 and email like '%_@_%'),
  role        org_role not null default 'member' check (role in ('admin', 'member')),
  invited_by  uuid references auth.users on delete set null,
  created_at  timestamptz not null default now(),
  expires_at  timestamptz not null default now() + interval '7 days',
  accepted_at timestamptz,
  accepted_by uuid references auth.users on delete set null
);
create index if not exists org_invites_org on public.org_invites (org_id, created_at desc);
create index if not exists org_invites_email on public.org_invites (email) where accepted_at is null;
create index if not exists org_invites_invited_by on public.org_invites (invited_by) where invited_by is not null;
create index if not exists org_invites_accepted_by on public.org_invites (accepted_by) where accepted_by is not null;
create unique index if not exists org_invites_one_open on public.org_invites (org_id, email) where accepted_at is null;

alter table public.org_invites enable row level security;
revoke all on public.org_invites from anon, authenticated;
grant select, delete on public.org_invites to authenticated;
drop policy if exists invites_read on public.org_invites;
create policy invites_read on public.org_invites for select to authenticated using (is_admin(org_id));
drop policy if exists invites_revoke on public.org_invites;
create policy invites_revoke on public.org_invites for delete to authenticated
  using (accepted_at is null and (is_owner(org_id) or (is_admin(org_id) and role = 'member')));

-- No direct membership inserts by users: accept_invite() (and onboarding) are the only ways in.
drop policy if exists members_insert on public.org_members;
revoke insert on public.org_members from authenticated, anon;

-- Invite an email address to `o` as `r` (owners: admin or member; admins: member only). Re-inviting the same open
-- address refreshes the invitation. Returns the invitation id.
create or replace function public.create_invite(o uuid, invitee_email text, r org_role default 'member')
  returns uuid language plpgsql security definer set search_path = public, pg_temp as
$$
declare
  uid uuid := auth.uid();
  e text := lower(trim(invitee_email));
  iid uuid;
begin
  if uid is null or not (is_owner(o) or (is_admin(o) and r = 'member')) then
    raise exception 'owners invite admins and members; admins invite members' using errcode = '42501';
  end if;
  if r = 'owner' then
    raise exception 'invite as admin or member; promote to owner once they have joined' using errcode = '22023';
  end if;
  if e is null or e !~ '^[^@\s]+@[^@\s]+$' then
    raise exception 'not an email address' using errcode = '22023';
  end if;
  if exists (select 1 from org_members m join auth.users u on u.id = m.user_id
              where m.org_id = o and lower(u.email) = e) then
    raise exception 'already a member' using errcode = '23505';
  end if;
  insert into org_invites (org_id, email, role, invited_by) values (o, e, r, uid)
  on conflict (org_id, email) where accepted_at is null
  do update set role = excluded.role, invited_by = excluded.invited_by, created_at = now(),
                expires_at = now() + interval '7 days'
  returning id into iid;
  insert into audit_log (org_id, actor, action, target, detail)
  values (o, uid::text, 'member.invite', e, jsonb_build_object('role', r, 'invite', iid));
  return iid;
end $$;

-- Open invitations addressed to the signed-in user's confirmed email (with the organization's name).
create or replace function public.my_invites()
  returns table (id uuid, org_id uuid, org_name text, role org_role, created_at timestamptz, expires_at timestamptz)
  language sql stable security definer set search_path = public, pg_temp as
$$
  select i.id, i.org_id, o.name, i.role, i.created_at, i.expires_at
    from org_invites i join orgs o on o.id = i.org_id join auth.users u on u.id = auth.uid()
   where u.email_confirmed_at is not null and i.email = lower(u.email)
     and i.accepted_at is null and i.expires_at > now()
   order by i.created_at desc
$$;

-- Accept an invitation: only its addressee (signed in, email confirmed), once, before it expires.
create or replace function public.accept_invite(invite uuid) returns uuid
  language plpgsql security definer set search_path = public, pg_temp as
$$
declare
  uid uuid := auth.uid();
  inv record;
begin
  if uid is null then
    raise exception 'sign in to accept an invitation' using errcode = '42501';
  end if;
  select i.* into inv from org_invites i join auth.users u on u.id = uid
   where i.id = invite and i.email = lower(u.email) and u.email_confirmed_at is not null
   for update of i;
  if not found then
    raise exception 'no such invitation for this account' using errcode = '42501';
  end if;
  if inv.accepted_at is not null or inv.expires_at <= now() then
    raise exception 'this invitation was already used or has expired' using errcode = '22023';
  end if;
  insert into org_members (org_id, user_id, role) values (inv.org_id, uid, inv.role)
  on conflict (org_id, user_id) do nothing;
  update org_invites set accepted_at = now(), accepted_by = uid where id = inv.id;
  insert into audit_log (org_id, actor, action, target, detail)
  values (inv.org_id, uid::text, 'member.join', uid::text, jsonb_build_object('role', inv.role, 'invite', inv.id));
  return inv.org_id;
end $$;

revoke execute on function public.create_invite(uuid, text, org_role), public.my_invites(),
  public.accept_invite(uuid) from public, anon;
grant execute on function public.create_invite(uuid, text, org_role), public.my_invites(),
  public.accept_invite(uuid) to authenticated;

-- --------------------------------------------------------------------------------------------- B17
-- At most 20 active (unrevoked) API keys per organization, whoever creates them (Console, CLI, SQL). A per-org
-- advisory lock makes concurrent inserts count one after another. Revoke a key to make room.
create or replace function public.api_keys_org_cap() returns trigger
  language plpgsql security definer set search_path = public, pg_temp as
$$
begin
  perform pg_advisory_xact_lock(hashtextextended('lorde.api_keys:' || new.org_id::text, 0));
  if (select count(*) from api_keys where org_id = new.org_id and revoked_at is null) >= 20 then
    raise exception 'an organization can have at most 20 active API keys; revoke one first'
      using errcode = '54000', hint = 'api_key_limit';
  end if;
  return new;
end $$;

drop trigger if exists api_keys_org_cap on public.api_keys;
create trigger api_keys_org_cap before insert on public.api_keys
  for each row execute function public.api_keys_org_cap();
revoke execute on function public.api_keys_org_cap() from public, anon, authenticated;
