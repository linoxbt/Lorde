-- Row-level security (spec Section 13.2): members read their org's rows; owners/admins write keys and
-- settings; the Gateway and Worker (service role) are the only writers of jobs, judgments and usage.

create or replace function public.is_member(o uuid) returns boolean
  language sql stable security definer set search_path = public, pg_temp as
$$ select exists (select 1 from org_members where org_id = o and user_id = auth.uid()) $$;

create or replace function public.is_admin(o uuid) returns boolean
  language sql stable security definer set search_path = public, pg_temp as
$$ select exists (select 1 from org_members where org_id = o and user_id = auth.uid()
                  and role in ('owner', 'admin')) $$;

alter table orgs           enable row level security;
alter table org_members    enable row level security;
alter table api_keys       enable row level security;
alter table datasets       enable row level security;
alter table jobs           enable row level security;
alter table judgments      enable row level security;
alter table human_labels   enable row level security;
alter table usage_events   enable row level security;
alter table model_versions enable row level security;
alter table audit_log      enable row level security;

create policy orgs_read   on orgs for select to authenticated using (is_member(id));
create policy orgs_update on orgs for update to authenticated using (is_admin(id)) with check (is_admin(id));

create policy members_read  on org_members for select to authenticated using (is_member(org_id));
create policy members_write on org_members for all to authenticated
  using (is_admin(org_id)) with check (is_admin(org_id));

create policy keys_read  on api_keys for select to authenticated using (is_member(org_id));
create policy keys_write on api_keys for insert to authenticated with check (is_admin(org_id));
create policy keys_revoke on api_keys for update to authenticated using (is_admin(org_id)) with check (is_admin(org_id));

create policy datasets_read   on datasets for select to authenticated using (is_member(org_id));
create policy datasets_insert on datasets for insert to authenticated with check (is_member(org_id));
create policy datasets_delete on datasets for delete to authenticated using (is_admin(org_id));

create policy jobs_read      on jobs      for select to authenticated using (is_member(org_id));
create policy judgments_read on judgments for select to authenticated
  using (exists (select 1 from jobs j where j.id = job_id and is_member(j.org_id)));
create policy usage_read     on usage_events for select to authenticated using (is_member(org_id));
create policy audit_read     on audit_log for select to authenticated using (is_admin(org_id));

create policy labels_read   on human_labels for select to authenticated using (is_member(org_id));
create policy labels_insert on human_labels for insert to authenticated
  with check (is_member(org_id) and labeler = auth.uid());

create policy models_read on model_versions for select to authenticated using (true);

-- Column privileges: the key hash never leaves the database for users.
revoke all on all tables in schema public from anon, authenticated;
grant select, update (name, retention_days, store_content) on orgs to authenticated;
grant select, insert, update, delete on org_members to authenticated;
grant select (id, org_id, name, prefix, scopes, rpm, created_by, created_at, last_used_at, revoked_at)
  on api_keys to authenticated;
grant insert (org_id, name, prefix, key_hash, scopes, rpm, created_by) on api_keys to authenticated;
grant update (revoked_at, name, rpm) on api_keys to authenticated;
grant select, insert, delete on datasets to authenticated;
grant select on jobs, judgments, usage_events, model_versions, audit_log to authenticated;
grant select, insert on human_labels to authenticated;
grant usage, select on all sequences in schema public to authenticated;

-- Storage: private buckets; object paths start with the org id.
insert into storage.buckets (id, name, public) values ('datasets', 'datasets', false), ('results', 'results', false)
  on conflict (id) do nothing;

create policy datasets_objects_read on storage.objects for select to authenticated
  using (bucket_id in ('datasets', 'results') and is_member(((storage.foldername(name))[1])::uuid));
create policy datasets_objects_upload on storage.objects for insert to authenticated
  with check (bucket_id = 'datasets' and is_member(((storage.foldername(name))[1])::uuid));
