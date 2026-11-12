/**
 * Optional CAPTCHA for supabase-js auth calls (pure: unit-tested in tests/unit/captcha.test.ts). Without a Turnstile
 * site key nothing is added and nothing is required; with one, every sign-up, sign-in, email link, resend and reset
 * request must carry a solved, single-use token (Supabase Auth checks it with the matching secret key).
 */
export class CaptchaMissing extends Error {}

export function captchaFields(siteKey: string, token: string | null): { captchaToken?: string } {
  if (!siteKey) return {};
  if (!token) throw new CaptchaMissing("captcha");
  return { captchaToken: token };
}
