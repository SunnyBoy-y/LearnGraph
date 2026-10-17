import { LoaderCircle } from "lucide-react";

export function VoiceActivityIndicator({ activity }: { activity: "idle" | "thinking" | "delegating" }) {
  if (activity !== "thinking" && activity !== "delegating") return null;
  return <span className="ml-2 inline-flex items-center gap-1.5 rounded-full bg-primary/8 px-2 py-0.5 text-primary" data-voice-activity={activity}>
    <LoaderCircle aria-hidden="true" className="size-3 motion-safe:animate-spin" />
    {activity === "delegating" ? "正在安排后台任务" : "思考中"}
  </span>;
}
