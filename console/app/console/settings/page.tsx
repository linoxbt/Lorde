import { PageHeader } from "@/components/console/bits";
import { SettingsView } from "@/components/console/settings-view";
import { getWorkspace, listMembers } from "@/lib/data";

export const metadata = { title: "Settings" };

export default async function Settings() {
  const [ws, members] = await Promise.all([getWorkspace(), listMembers()]);
  return (
    <>
      <PageHeader title="Settings" lead="Organization, members, retention and data deletion." />
      <SettingsView org={ws.org} members={members} role={ws.me.role} meId={ws.me.id} demo={ws.mode === "demo"} />
    </>
  );
}
