import { documentMarkdown } from "@/genui/document-export";
import { stripRawWebCitations } from "@/lib/document-citations";
import { committedNumericPreview, parseUISnapshot, visibleStreamUIText } from "@/lib/stream-ui";
import type { MessagePart } from "@/types/sessions";
import { isPlaceholderAcknowledgement, orderedMessageParts, placeRichResponseParts } from "./chat-message-parts";

/** Copy persisted content, including collapsed document sections and full visual data. */
export function messageCopyText(parts: MessagePart[], fallback = ""): string {
  if (!parts.length) return stripRawWebCitations(visibleStreamUIText(fallback, false));
  return placeRichResponseParts(orderedMessageParts(parts)).map(part => {
    if (isPlaceholderAcknowledgement(part)) return "";
    if (part.type === "ui") {
      const snapshot = parseUISnapshot(part.data);
      return snapshot && snapshot.state !== "removed"
        ? documentMarkdown(committedNumericPreview(snapshot)).trim()
        : "";
    }
    if (part.type === "sticker") return `[表情：${String(part.data?.alt ?? "表情包")}]`;
    if (part.type === "text" || part.type === "acknowledgement") {
      const streaming = part.status === "streaming";
      return stripRawWebCitations(visibleStreamUIText(part.content ?? "", streaming), streaming);
    }
    return "";
  }).filter(text => text.trim() && text.trim() !== "正在思考").join("\n\n");
}
