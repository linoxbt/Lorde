import { Empty, PageHeader } from "@/components/console/bits";
import { ReviewQueue } from "@/components/console/review-queue";
import { getWorkspace, reviewData } from "@/lib/data";

export const metadata = { title: "Review" };

export default async function Review() {
  const [ws, review] = await Promise.all([getWorkspace(), reviewData()]);
  const demo = ws.mode === "demo";
  const nothing = review.items.length === 0 && review.labels.length === 0;
  return (
    <>
      <PageHeader title="Review" lead="Label judged rows with the gate's own rubric. Agreement and κ with Lorde update as you go: the human check on the judge." />
      {nothing ? (
        <Empty title="Nothing to review yet">
          {ws.org.store_content
            ? "Rows appear here after a job finishes while content retention is on. Start a job from New job."
            : <>Lorde stores scores only by default. Turn on “Store judged content” in Settings, then run a job: its rows appear here
              to label. Or label offline with <code className="mx-1 rounded bg-wash px-1 font-mono">forge/04_label_tool.py</code>.</>}
        </Empty>
      ) : (
        <ReviewQueue items={review.items} initialLabels={review.labels} demo={demo} orgId={ws.org.id} userId={ws.me.id} />
      )}
    </>
  );
}
