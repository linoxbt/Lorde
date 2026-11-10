import { PageHeader } from "@/components/console/bits";
import { NewJobForm } from "@/components/console/new-job-form";
import { getJudgments, getWorkspace } from "@/lib/data";

export const metadata = { title: "New job" };

export default async function NewJob(props: PageProps<"/console/jobs/new">) {
  const sp = await props.searchParams;
  const ws = await getWorkspace();
  const last = ws.jobs.find((j) => j.status === "succeeded");
  const sample = last ? (await getJudgments(last.id)).slice(0, 200) : [];
  return (
    <>
      <PageHeader title="New job" lead="Pick a dataset, choose gates and thresholds, and Lorde gates every row through CuratorKIT." />
      <NewJobForm datasets={ws.datasets} sample={sample} demo={ws.mode === "demo"}
        preselect={typeof sp.dataset === "string" ? sp.dataset : undefined} sampleFrom={last?.dataset_name} />
    </>
  );
}
