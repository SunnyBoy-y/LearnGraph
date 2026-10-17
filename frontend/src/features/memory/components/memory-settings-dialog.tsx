import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
export function MemorySettingsDialog({ exportBusy, open, onExport, onOpenGovernance, onOpenTrash, onOpenChange }: {
 exportBusy:boolean;open:boolean;onExport:()=>void;onOpenGovernance:()=>void;onOpenTrash:()=>void;onOpenChange:(open:boolean)=>void
}) {return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent><DialogHeader><DialogTitle>记忆管理</DialogTitle><DialogDescription>读取、写入与检索策略统一在记忆与检索中设置。模型统一在模型与服务中选择。</DialogDescription></DialogHeader>
<div className="flex flex-wrap gap-2"><Button onClick={()=>{onOpenChange(false);onOpenGovernance()}}>记忆与检索设置</Button><Button variant="outline" disabled={exportBusy} onClick={onExport}>导出记忆</Button><Button variant="outline" onClick={()=>{onOpenChange(false);onOpenTrash()}}>查看回收站</Button></div></DialogContent></Dialog>}
