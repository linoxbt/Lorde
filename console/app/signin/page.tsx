import type { Metadata } from "next";

import { AuthPage } from "@/components/console/auth-page";
import { SignInForm } from "@/components/console/signin-form";
import { signinMessage } from "@/lib/auth-errors";
import { supabaseEnabled } from "@/lib/env";
import { safeNext } from "@/lib/safe-next";

export const metadata: Metadata = { title: "Sign in" };

export default async function SignIn(props: PageProps<"/signin">) {
  const sp = await props.searchParams;
  // Only fixed messages for known codes: text from the URL is never shown.
  const message = signinMessage(sp.error);
  return (
    <AuthPage title="Sign in to Lorde" lead="Use the method you signed up with."
      error={message?.tone === "error" ? message.text : undefined} notice={message?.tone === "notice" ? message.text : undefined}>
      <SignInForm enabled={supabaseEnabled()} next={safeNext(sp.next)} mode="signin" />
    </AuthPage>
  );
}
