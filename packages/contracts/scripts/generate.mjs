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
    ["--write", destination],
    {
      cwd: root,
      stdio: "pipe",
    },
  );
  const generated = await fs.readFile(destination, "utf8");
  if (check) {
    if ((await fs.readFile(output, "utf8").catch(() => "")) !== generated)
      throw new Error(
        "Generated TypeScript drift. Run npm run contracts:generate.",
      );
  } else {
    await fs.writeFile(output, generated);
  }
  console.log(
    check ? "Generated TypeScript matches." : "Generated TypeScript exported.",
  );
} finally {
  await fs.rm(temporary, { recursive: true, force: true });
}
