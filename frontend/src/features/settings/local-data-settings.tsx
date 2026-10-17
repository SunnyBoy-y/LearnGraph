import { Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Surface } from "@/components/shared/page-elements";
import { Button } from "@/components/ui/button";
import { clearSelectionExplanations } from "@/features/chat/selection-explanation";
export function LocalDataSettings() {
    const [confirm, setConfirm] = useState(false);
    return <Surface className="mt-5 p-5">
        <div className="flex items-start gap-3">
          <Trash2 className="mt-0.5 size-5 shrink-0 text-destructive"/>
          <div className="min-w-0 text-sm leading-6 text-muted-foreground">
            <p className="font-medium text-foreground">清除本设备学习痕迹</p>
            <p className="mt-1">
              划词解释记录（选中文本及上下文）保存在浏览器本地，按当前登录账户和工作区隔离。
              点击下方按钮将清除当前账户与工作区在本设备上的划词历史记录，此操作不可撤销，不会删除云端记忆。
            </p>
            <Button className="mt-3" onClick={() => {
            if (!confirm) {
                setConfirm(true);
                return;
            }
            clearSelectionExplanations();
            setConfirm(false);
            toast.success("已清除本设备上的划词学习痕迹");
        }} size="sm" type="button" variant="destructive">
              <Trash2 className="size-4"/>
              {confirm ? "确认清除记录" : "清除记录"}
            </Button>{confirm ? <Button className="mt-3 ml-2" variant="outline" size="sm" onClick={() => setConfirm(false)}>取消</Button> : null}
          </div>
        </div>
      </Surface>;
}
