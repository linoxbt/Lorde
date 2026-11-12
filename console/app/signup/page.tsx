import type { Metadata } from "next";

import { AuthPage } from "@/components/console/auth-page";
import { SignInForm } from "@/components/console/signin-form";
import { supabaseEnabled } from "@/lib/env";
import { safeNext } from "@/lib/safe-next";

export const metadata: Metadata = { title: "Create account" };

export default async function SignUp(props: PageProps<"/signup">) {
  const sp = await props.searchParams;
  return (
    <AuthPage title="Create your Lorde account" lead="Free to start. An organization is created for you, and you are its owner.">
      <SignInForm enabled={supabaseEnabled()} next={safeNext(sp.next)} mode="signup" />
    </AuthPage>
  );
}
