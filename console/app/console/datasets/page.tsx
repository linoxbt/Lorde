import { PageHeader } from "@/components/console/bits";
import { DatasetsView } from "@/components/console/datasets-view";
import { getWorkspace } from "@/lib/data";

export const metadata = { title: "Datasets" };

export default async function Datasets() {
  const ws = await getWorkspace();
  return (
    <>
      <PageHeader title="Datasets" lead={`Upload JSONL, CSV or Parquet up to 200 MB. Files are private to ${ws.org.name} and deleted after ${ws.org.retention_days} days.`} />
      <DatasetsView initial={ws.datasets} demo={ws.mode === "demo"} />
    </>
  );
}
