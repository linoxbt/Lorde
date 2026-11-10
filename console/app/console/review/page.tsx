import { Empty, PageHeader } from "@/components/console/bits";
import { ReviewQueue } from "@/components/console/review-queue";
import { getWorkspace } from "@/lib/data";
import { demoReview } from "@/lib/demo";

export const metadata = { title: "Review" };

export default async function Review() {
  const ws = await getWorkspace();
  return (
    <>
      <PageHeader title="Review" lead="Label a sample with the gate's own rubric. Agreement and κ with Lorde update as you go: the human check on the judge." />
      {ws.mode === "demo" || ws.org.store_content ? (
        <ReviewQueue items={demoReview} demo={ws.mode === "demo"} orgId={ws.org.id} />
      ) : (
        <Empty title="Review needs judged content">
          Lorde stores scores only by default. Enable content retention in Settings to review rows here, or label offline with
          <code className="mx-1 rounded bg-wash px-1 font-mono">forge/04_label_tool.py</code>.
        </Empty>
      )}
    </>
  );
}
