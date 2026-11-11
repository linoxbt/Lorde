import { PageHeader } from "@/components/console/bits";
import { KeysView } from "@/components/console/keys-view";
import { getWorkspace, listKeys } from "@/lib/data";

export const metadata = { title: "API keys" };

export default async function Keys() {
  const [ws, keys] = await Promise.all([getWorkspace(), listKeys()]);
  const canWrite = ws.me.role === "owner" || ws.me.role === "admin";
  return (
    <>
      <PageHeader title="API keys" lead="Keys are scoped per organization and shown once. Only a prefix and a peppered hash are stored; revocation takes effect within a minute." />
      <KeysView initial={keys.rows} total={keys.total} canWrite={canWrite} />
    </>
  );
}
