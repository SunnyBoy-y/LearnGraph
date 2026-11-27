import { useState } from "react";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";

type Confirmation = { title: string; description: string; label: string; run: () => void; destructive?: boolean };

export function useActionConfirmation() {
  const [pending, confirm] = useState<Confirmation | null>(null);
  const dialog = <AlertDialog open={pending !== null} onOpenChange={(open) => { if (!open) confirm(null); }}>
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogTitle>{pending?.title}</AlertDialogTitle>
        <AlertDialogDescription>{pending?.description}</AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel>取消</AlertDialogCancel>
        <AlertDialogAction variant={pending?.destructive ? "destructive" : "default"} onClick={() => pending?.run()}>{pending?.label}</AlertDialogAction>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>;
  return { confirm, dialog };
}
