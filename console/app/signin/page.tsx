import type { Metadata } from "next";

import { AuthPage, safeNext } from "@/components/console/auth-page";
import { SignInForm } from "@/components/console/signin-form";
import { supabaseEnabled } from "@/lib/env";

export const metadata: Metadata = { title: "Sign in" };

export default async function SignIn(props: PageProps<"/signin">) {
  const sp = await props.searchParams;
  const error = typeof sp.error === "string" ? sp.error : undefined;
  return (
    <AuthPage title="Sign in to Lorde" lead="Use the method you signed up with." error={error}>
      <SignInForm enabled={supabaseEnabled()} next={safeNext(sp.next)} mode="signin" />
    </AuthPage>
  );
}
