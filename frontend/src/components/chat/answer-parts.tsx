import type { ReactNode } from "react";
import { ChatStreamPartRenderer } from "./chat-stream-part-renderer";
import { groupAnswerParts, QuestionSetPager } from "./question-set-pager";
import { SandboxImageStrip } from "./sandbox-image-artifact";
import type { TrustedComponentAction } from "./trusted-component-renderer";
import type { MessagePart } from "@/types/sessions";

/** The answer body shared by ordinary messages and managed child results. */
export function AnswerParts({ parts, siblingParts = parts, interactive = true, onAction, renderPart }: {
  parts: MessagePart[];
  siblingParts?: MessagePart[];
  interactive?: boolean;
  onAction?: (action: TrustedComponentAction) => void | Promise<void>;
  renderPart?: (part: MessagePart) => ReactNode;
}) {
  const render = renderPart ?? ((part: MessagePart) => <ChatStreamPartRenderer
    key={part.id} part={part} siblingParts={siblingParts} interactive={interactive}
    onAction={onAction} streaming={part.status === "streaming" || part.status === "pending"} />);
  return <>{groupAnswerParts(parts).map((group) => {
    if (group.kind === "question_set") return <QuestionSetPager
      key={`question-set-${group.parts.map(part => part.id).join("-")}`}
      questions={group.questions}
      onSubmit={submission => void onAction?.({
        componentId: group.questions[0]?.componentId ?? group.parts[0].id,
        componentType: group.questions.length > 1 ? "question_set" : group.questions[0]?.componentType ?? "question_set",
        event: "submit",
        payload: { answer: submission.summaryText, summaryText: submission.summaryText, results: submission.results,
          labels: submission.results.flatMap(item => item.labels), graded_count: submission.gradedCount, correct_count: submission.correctCount },
      })} />;
    if (group.kind === "image_strip") return <SandboxImageStrip key={`image-strip-${group.parts[0]?.id ?? "empty"}`} parts={group.parts} />;
    if (group.kind === "side_pair") return <div className="grid gap-3 sm:grid-cols-2 sm:items-start" key={`side-pair-${group.artifact.id}`}>
      <div>{render(group.text)}</div><div>{render(group.artifact)}</div>
    </div>;
    return render(group.part);
  })}</>;
}
