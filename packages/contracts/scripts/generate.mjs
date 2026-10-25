import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const packageRoot = path.join(root, "packages/contracts");
const output = path.join(packageRoot, "src/generated.ts");
const check = process.argv.includes("--check");
const temporary = await fs.mkdtemp(
  path.join(os.tmpdir(), "penforge-contracts-"),
);
try {
  const destination = path.join(temporary, "generated.ts");
  execFileSync(
    path.join(root, "node_modules/.bin/openapi-typescript"),
    [
      path.join(packageRoot, "openapi.json"),
      "--output",
      destination,
      "--default-non-nullable",
      "false",
    ],
    { cwd: root, stdio: "pipe" },
  );
  // Formatting is pinned by the existing package lock and shared with source files.
  execFileSync(
    path.join(root, "node_modules/.bin/prettier"),
    ["--no-config", "--no-editorconfig", "--write", destination],
    {
      cwd: root,
      stdio: "pipe",
    },
  );
  const generated = await fs.readFile(destination, "utf8");
  if (check) {
    const committed = await fs.readFile(output, "utf8").catch(() => "");
    if (committed !== generated) {
      const before = committed.split("\n");
      const after = generated.split("\n");
      const line = Array.from(
        { length: Math.max(before.length, after.length) },
        (_, index) => index,
      ).find((index) => before[index] !== after[index]);
      throw new Error(
        `Generated TypeScript drift at line ${line + 1}. Run npm run contracts:generate.\n` +
          `Committed: ${JSON.stringify(before[line] ?? "<EOF>")}\n` +
          `Generated: ${JSON.stringify(after[line] ?? "<EOF>")}`,
      );
    }
  } else {
    await fs.writeFile(output, generated);
  }
  console.log(
    check ? "Generated TypeScript matches." : "Generated TypeScript exported.",
  );
} finally {
  await fs.rm(temporary, { recursive: true, force: true });
}
