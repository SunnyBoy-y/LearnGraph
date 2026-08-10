// lucide-react ships `dist/lucide-react.d.ts` via the legacy `typings` field,
// but TypeScript 6 bundler resolution misses it when the package has no
// `exports`/`types` entry. Re-export the shipped declarations so every named
// icon import keeps its type.
declare module "lucide-react" {
  export * from "lucide-react/dist/lucide-react";
}
