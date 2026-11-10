import { PageHeader } from "@/components/console/bits";
import { Playground } from "@/components/console/playground";
import { getWorkspace } from "@/lib/data";

export const metadata = { title: "Playground" };

export default async function PlaygroundPage() {
  const ws = await getWorkspace();
  return (
    <>
      <PageHeader title="Playground" lead="Judge one item with CuratorKIT's exact prompt and see what CuratorKIT would do with the reply." />
      <Playground demo={ws.mode === "demo"} />
    </>
  );
}
