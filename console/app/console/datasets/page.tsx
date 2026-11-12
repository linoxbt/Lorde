import { PageHeader } from "@/components/console/bits";
import { DatasetsView } from "@/components/console/datasets-view";
import { getWorkspace, listDatasets } from "@/lib/data";
import { DEMO_NOW } from "@/lib/demo";
import { env } from "@/lib/env";

export const metadata = { title: "Datasets" };

export default async function Datasets() {
  const [ws, ds] = await Promise.all([getWorkspace(), listDatasets()]);
  return (
    <>
      <PageHeader title="Datasets" lead={`Upload JSONL, CSV or Parquet up to 200 MB. Files are private to ${ws.org.name} and deleted after ${ws.org.retention_days} days.`} />
      <DatasetsView rows={ds.rows} total={ds.total} demo={ws.mode === "demo"} apiBase={env.publicApiUrl}
        clock={ws.mode === "demo" ? DEMO_NOW : undefined} />
    </>
  );
}
