import type { NextConfig } from "next";
import { existsSync } from "node:fs";
import { join } from "node:path";

const localCursive = process.env.PENFORGE_LOCAL_CURSIVE === "1";
if (
  !localCursive &&
  existsSync(join(process.cwd(), "public/fonts/local-demo"))
) {
  throw new Error(
    "Private demo fonts are present. Use PENFORGE_LOCAL_CURSIVE=1 for a localhost preview, or remove public/fonts/local-demo before a normal build/start.",
  );
}
if (localCursive) {
  for (const name of ["PenForge-Cursive.woff2", "FONT-README.txt"]) {
    if (!existsSync(join(process.cwd(), "public/fonts/local-demo", name))) {
      throw new Error(
        `Local cursive preview requires public/fonts/local-demo/${name}`,
      );
    }
  }
}

const config: NextConfig = {
  // Keep development HMR output separate from the production build.
  distDir:
    process.env.PENFORGE_NEXT_DIST_DIR ||
    (process.env.NODE_ENV === "development" ? ".next-dev" : ".next"),
  devIndicators: false,
  env: {
    NEXT_PUBLIC_PENFORGE_DEMO_FONT: localCursive ? "cursive" : "caveat",
  },
};

export default config;
