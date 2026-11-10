import { PageHeader } from "@/components/console/bits";
import { SettingsView } from "@/components/console/settings-view";
import { getWorkspace } from "@/lib/data";

export const metadata = { title: "Settings" };

export default async function Settings() {
  const ws = await getWorkspace();
  return (
    <>
      <PageHeader title="Settings" lead="Organization, members, retention and data deletion." />
      <SettingsView org={ws.org} members={ws.members} role={ws.me.role} demo={ws.mode === "demo"} />
    </>
  );
}
