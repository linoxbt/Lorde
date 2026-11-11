import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { AuthPage } from "@/components/console/auth-page";
import { ResetForm } from "@/components/console/reset-form";
import { supabaseEnabled } from "@/lib/env";

export const metadata: Metadata = { title: "Reset password", robots: { index: false } };
export const dynamic = "force-dynamic";

export default async function ResetPassword() {
  if (!supabaseEnabled()) redirect("/signin");
  const { supabaseServer } = await import("@/lib/supabase/server");
  const { data } = await (await supabaseServer()).auth.getUser();
  if (!data.user) redirect("/signin?error=" + encodeURIComponent("This reset link has expired. Request a new one."));
  return (
    <AuthPage title="Choose a new password" lead={`For ${data.user.email}.`}>
      <ResetForm />
    </AuthPage>
  );
}
