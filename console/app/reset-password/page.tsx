import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { AuthPage } from "@/components/console/auth-page";
import { ResetForm } from "@/components/console/reset-form";
import { supabaseEnabled } from "@/lib/env";

export const metadata: Metadata = { title: "Reset password", robots: { index: false } };
export const dynamic = "force-dynamic";

export default async function ResetPassword(props: PageProps<"/reset-password">) {
  if (!supabaseEnabled()) redirect("/signin");
  const invited = (await props.searchParams).invited === "1";  // an invite link: the user has no password yet
  const { supabaseServer } = await import("@/lib/supabase/server");
  const { data } = await (await supabaseServer()).auth.getUser();
  if (!data.user) redirect("/signin?error=link_expired");
  return (
    <AuthPage title={invited ? "Choose a password" : "Choose a new password"} lead={`For ${data.user.email}.`}>
      <ResetForm />
    </AuthPage>
  );
}
