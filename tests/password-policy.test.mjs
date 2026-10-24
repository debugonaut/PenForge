import { createTypeScriptLoader } from "./helpers/typescript.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";

const moduleUrl = createTypeScriptLoader(import.meta.url);
const importTypeScript = async (relative) => import(await moduleUrl(relative));
const policy = await importTypeScript("../src/lib/password-policy.ts");

const met = (value) =>
  Object.fromEntries(policy.passwordRules(value).map((rule) => [rule.id, rule.met]));

test("the policy requires length, both letter cases and a number", () => {
  assert.equal(policy.PASSWORD_MIN_LENGTH, 8);
  assert.deepEqual(met(""), {
    length: false,
    uppercase: false,
    lowercase: false,
    digit: false,
  });
  assert.deepEqual(met("abcdefgh"), {
    length: true,
    uppercase: false,
    lowercase: true,
    digit: false,
  });
  assert.deepEqual(met("Abcdefg1"), {
    length: true,
    uppercase: true,
    lowercase: true,
    digit: true,
  });
  assert.equal(policy.passwordMeetsPolicy("Abcdefg1"), true);
  assert.equal(policy.passwordMeetsPolicy("Abcdef1"), false);
  assert.equal(policy.passwordMeetsPolicy("abcdefg1"), false);
  assert.equal(policy.passwordMeetsPolicy("ABCDEFG1"), false);
  assert.equal(policy.passwordMeetsPolicy("Abcdefgh"), false);
});

test("the rule list keeps stable ids, labels and order for the checklist", () => {
  assert.deepEqual(
    policy.passwordRules("x").map(({ id, label }) => [id, label]),
    [
      ["length", "At least 8 characters"],
      ["uppercase", "One uppercase letter"],
      ["lowercase", "One lowercase letter"],
      ["digit", "One number"],
    ],
  );
});

test("case and digit checks use Unicode properties rather than ASCII ranges", () => {
  assert.deepEqual(met("Éclair12"), {
    length: true,
    uppercase: true,
    lowercase: true,
    digit: true,
  });
  assert.equal(policy.passwordRules("àéî1").find((r) => r.id === "uppercase").met, false);
  assert.equal(
    policy.passwordRules("Aaaaaaa٣").find((r) => r.id === "digit").met,
    true,
  );
});

test("length counts characters, not UTF-16 code units", () => {
  assert.equal(policy.passwordRules("abcdefg").find((r) => r.id === "length").met, false);
  assert.equal(
    policy.passwordRules("Ééèêëàâ1").find((r) => r.id === "length").met,
    true,
  );
});

test("confirmation must be present and exactly equal", () => {
  assert.equal(policy.passwordsMatch("Abcdefg1", "Abcdefg1"), true);
  assert.equal(policy.passwordsMatch("Abcdefg1", "abcdefg1"), false);
  assert.equal(policy.passwordsMatch("", ""), false);
  assert.equal(policy.passwordsMatch("Abcdefg1", ""), false);
});
