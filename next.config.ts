import type { NextConfig } from "next";

const config: NextConfig = {
  // Keep development HMR output separate from the production build.
  distDir: process.env.PENFORGE_NEXT_DIST_DIR || (process.env.NODE_ENV === "development" ? ".next-dev" : ".next"),
  devIndicators: false,
};

export default config;
