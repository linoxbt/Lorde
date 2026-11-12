import { PageHeader } from "@/components/console/bits";
import { NewJobForm } from "@/components/console/new-job-form";
import { getWorkspace, listDatasets, previewSample } from "@/lib/data";
import { datasetState, jobDatasetLabel } from "@/lib/datasets";
import { DEMO_NOW } from "@/lib/demo";

export const metadata = { title: "New job" };

export default async function NewJob(props: PageProps<"/console/jobs/new">) {
  const sp = await props.searchParams;
  const [ws, ds] = await Promise.all([getWorkspace(), listDatasets()]);
  const asked = typeof sp.dataset === "string" ? sp.dataset : undefined;
  // Expired datasets cannot start a job: never preselect one (the form also skips this browser's unfinished uploads).
  const clock = ws.mode === "demo" ? DEMO_NOW : undefined;
  const now = clock ?? Date.now();
  const usable = ds.rows.filter((d) => datasetState(d, now) === "ready");
  const selected = usable.find((d) => d.id === asked)?.id ?? usable[0]?.id;
  const preview = await previewSample(selected);
  const basis = !preview.from ? null : {
    rows: preview.sample.length, sameDataset: preview.sameDataset,
    name: jobDatasetLabel(preview.from), jobId: preview.from.id,
  };
  return (
    <>
      <PageHeader title="New job" lead="Pick a dataset, choose gates and thresholds, and Lorde gates every row through CuratorKIT." />
      <NewJobForm datasets={ds.rows} sample={preview.sample} demo={ws.mode === "demo"} selected={selected} basis={basis} clock={clock} />
    </>
  );
}
