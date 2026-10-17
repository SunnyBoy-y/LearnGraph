import type { ComponentProps, ReactNode } from "react";

import { FilePreviewContent } from "./file-preview-content";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

type FilePreviewDialogProps = ComponentProps<typeof FilePreviewContent> & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  description?: ReactNode;
};

export function FilePreviewDialog({ open, onOpenChange, description = "内置文件预览", ...props }: FilePreviewDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[min(92svh,58rem)] w-full max-w-[calc(100%-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-5xl" showCloseButton>
        <DialogHeader className="shrink-0 border-b px-5 py-4 pr-12">
          <DialogTitle className="truncate">{props.filename}</DialogTitle>
          <DialogDescription className="truncate">{description}</DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 overflow-auto bg-muted/15">
          {open ? <FilePreviewContent {...props} imageZoom={props.imageZoom ?? true} /> : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
