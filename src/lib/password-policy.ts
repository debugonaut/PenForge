/**
 * Account-screen guidance only. The configured identity provider remains the
 * authority for what it accepts; this policy never claims server enforcement.
 */
export const PASSWORD_MIN_LENGTH = 8;

const checks = [
  {
    id: "length",
    label: `At least ${PASSWORD_MIN_LENGTH} characters`,
    test: (value: string) => Array.from(value).length >= PASSWORD_MIN_LENGTH,
  },
  {
    id: "uppercase",
    label: "One uppercase letter",
    test: (value: string) => /\p{Lu}/u.test(value),
  },
  {
    id: "lowercase",
    label: "One lowercase letter",
    test: (value: string) => /\p{Ll}/u.test(value),
  },
  {
    id: "digit",
    label: "One number",
    test: (value: string) => /\p{Nd}/u.test(value),
  },
] as const;

export type PasswordRuleId = (typeof checks)[number]["id"];
export type PasswordRule = {
  id: PasswordRuleId;
  label: string;
  met: boolean;
};

export function passwordRules(value: string): PasswordRule[] {
  return checks.map(({ id, label, test }) => ({ id, label, met: test(value) }));
}

export function passwordMeetsPolicy(value: string): boolean {
  return checks.every(({ test }) => test(value));
}

export function passwordsMatch(
  password: string,
  confirmation: string,
): boolean {
  return password.length > 0 && password === confirmation;
}
