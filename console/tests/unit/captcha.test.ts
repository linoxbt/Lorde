import assert from "node:assert/strict";
import { test } from "node:test";

import { CaptchaMissing, captchaFields } from "../../lib/captcha.ts";

test("CAPTCHA is a no-op without a site key, and required with one", () => {
  assert.deepEqual(captchaFields("", null), {});
  assert.deepEqual(captchaFields("", "tok"), {});
  assert.throws(() => captchaFields("0x4AAA", null), CaptchaMissing);
  assert.deepEqual(captchaFields("0x4AAA", "tok"), { captchaToken: "tok" });
});
