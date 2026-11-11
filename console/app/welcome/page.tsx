import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { AuthPage } from "@/components/console/auth-page";
import { supabaseEnabled } from "@/lib/env";
import { primaryMembership } from "@/lib/membership";

export const metadata: Metadata = { title: "Organization", robots: { index: false } };
export const dynamic = "force-dynamic";

/** Signed in, but in no organization (removed from it, or onboarding did not finish): explain instead of crashing. */
export default async function Welcome() {
  if (!supabaseEnabled()) redirect("/console");
  const { supabaseServer } = await import("@/lib/supabase/server");
  const sb = await supabaseServer();
  const { data } = await sb.auth.getUser();
  if (!data.user) redirect("/signin");
  if (await primaryMembership(sb, data.user.id)) redirect("/console");
  return (
    <AuthPage title="No organization yet" lead={`Signed in as ${data.user.email ?? "your account"}.`}>
      <div className="space-y-4 text-[15px]">
        <p>
          Your account isn&apos;t in an organization yet. Every new account gets its own organization when it is created; if you
          were removed from one, or yours was not set up, ask an owner to add you, or contact support with the email above.
        </p>
        <div className="flex flex-wrap gap-2">
          <a href="/welcome" className="inline-flex h-10 items-center rounded-full bg-accent px-5 text-sm font-medium text-white hover:bg-accent-hover">Check again</a>
          <form action="/auth/signout" method="post">
            <button type="submit" className="inline-flex h-10 items-center rounded-full border border-line px-5 text-sm hover:bg-wash">Sign out</button>
          </form>
        </div>
      </div>
    </AuthPage>
  );
}
