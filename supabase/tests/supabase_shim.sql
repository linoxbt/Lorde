-- Minimal stand-ins for Supabase's auth and storage schemas, so migrations and RLS can be tested on
-- plain Postgres. Not applied in Supabase, which provides the real ones.
do $$ begin
  if not exists (select from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select from pg_roles where rolname = 'service_role') then create role service_role nologin bypassrls; end if;
end $$;
create schema if not exists auth;
create table if not exists auth.users (id uuid primary key default gen_random_uuid(), email text,
                                      email_confirmed_at timestamptz);
create or replace function auth.uid() returns uuid language sql stable as
$$ select nullif(current_setting('request.jwt.claims', true)::json ->> 'sub', '')::uuid $$;
grant usage on schema auth to anon, authenticated, service_role;
create schema if not exists storage;
create table if not exists storage.buckets (id text primary key, name text, public boolean default false);
create table if not exists storage.objects (id uuid primary key default gen_random_uuid(), bucket_id text,
                                            name text, owner uuid);
create or replace function storage.foldername(name text) returns text[] language sql immutable as
$$ select (string_to_array(name, '/'))[1:array_length(string_to_array(name, '/'), 1) - 1] $$;
alter table storage.objects enable row level security;
grant usage on schema storage, public to anon, authenticated, service_role;
grant select, insert on storage.objects to authenticated;
grant all on all tables in schema public to service_role;
