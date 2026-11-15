import { z } from "zod";

/** Identifiers only: AnswerIcon resolves bundled assets, never arbitrary URLs. */
export const legacyRailIconSchema = z.enum([
  "zap", "brain-circuit", "search", "layers", "shield", "lightbulb", "circle-dot",
  "check-check", "blocks", "sliders", "file-text", "database", "chart", "book-open", "target", "code",
]);
export const railIconSchema = z.union([
  legacyRailIconSchema,
  z.string().min(8).max(100).regex(/^fluent:[a-z0-9]+(?:-[a-z0-9]+)*$/).refine(value => value === value.trim()),
]);
export type RailIcon = z.infer<typeof railIconSchema>;
export const railTypes = new Set(["feature_list", "step_list", "status_list"]);
