export type SuggestedPromptErrorInput = {
  canPrepare: boolean;
  canRead: boolean;
  anchorError: boolean;
  persistedReadError: boolean;
  hasPersistedBatch: boolean;
  providerError: boolean;
  canGenerate: boolean;
  generationError: boolean;
};

export function shouldShowSuggestedPromptError({
  canPrepare,
  canRead,
  anchorError,
  persistedReadError,
  hasPersistedBatch,
  providerError,
  canGenerate,
  generationError,
}: SuggestedPromptErrorInput): boolean {
  return Boolean(
    canPrepare &&
      (anchorError ||
        (canRead &&
          (persistedReadError ||
            (!hasPersistedBatch &&
              (providerError || (canGenerate && generationError)))))),
  );
}
