import { z } from "zod";

/** Static imports only: model data cannot request arbitrary icons or assets. */
export const railIconSchema = z.enum([
  "zap", "brain-circuit", "search", "layers", "shield", "lightbulb", "circle-dot",
  "check-check", "blocks", "sliders", "file-text", "database", "chart", "book-open", "target", "code",
]);
export type RailIcon = z.infer<typeof railIconSchema>;
export const railTypes = new Set(["feature_list", "step_list", "status_list"]);
