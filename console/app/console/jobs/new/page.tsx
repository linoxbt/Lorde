import { PageHeader } from "@/components/console/bits";
import { NewJobForm } from "@/components/console/new-job-form";
import { getWorkspace, listDatasets, previewSample } from "@/lib/data";
import { jobDatasetLabel } from "@/lib/datasets";

export const metadata = { title: "New job" };

export default async function NewJob(props: PageProps<"/console/jobs/new">) {
  const sp = await props.searchParams;
  const [ws, ds] = await Promise.all([getWorkspace(), listDatasets()]);
  const asked = typeof sp.dataset === "string" ? sp.dataset : undefined;
  const selected = ds.rows.find((d) => d.id === asked)?.id ?? ds.rows[0]?.id;
  const preview = await previewSample(selected);
  const basis = !preview.from ? null : {
    rows: preview.sample.length, sameDataset: preview.sameDataset,
    name: jobDatasetLabel(preview.from), jobId: preview.from.id,
  };
  return (
    <>
      <PageHeader title="New job" lead="Pick a dataset, choose gates and thresholds, and Lorde gates every row through CuratorKIT." />
      <NewJobForm datasets={ds.rows} sample={preview.sample} demo={ws.mode === "demo"} selected={selected} basis={basis} />
    </>
  );
}
