-- Audit every API key creation and revocation in the database itself (spec Section 14.1), whichever
-- path made the change: the Console (user session under RLS), the Gateway CLI or SQL.
create or replace function public.audit_api_keys() returns trigger
  language plpgsql security definer set search_path = public, pg_temp as
$$
declare actor text := coalesce(auth.uid()::text, nullif(current_setting('lorde.actor', true), ''), 'system');
begin
  if tg_op = 'INSERT' then
    insert into audit_log (org_id, actor, action, target, detail)
    values (new.org_id, actor, 'key.create', new.prefix, jsonb_build_object('name', new.name, 'scopes', new.scopes));
  elsif tg_op = 'UPDATE' and old.revoked_at is null and new.revoked_at is not null then
    insert into audit_log (org_id, actor, action, target) values (new.org_id, actor, 'key.revoke', new.prefix);
  end if;
  return new;
end $$;

create trigger api_keys_audit after insert or update on api_keys
  for each row execute function public.audit_api_keys();
