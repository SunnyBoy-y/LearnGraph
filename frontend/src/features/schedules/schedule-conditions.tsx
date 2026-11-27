import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { EventPredicate } from "@/types/schedules";

const operators: Record<EventPredicate["op"], string> = {
  eq: "等于", ne: "不等于", contains: "包含", in: "属于列表", gt: "大于", gte: "大于或等于", lt: "小于", lte: "小于或等于", exists: "存在",
};

export function ScheduleConditions({ conditions, onChange, paths, required }: {
  conditions: EventPredicate[];
  onChange: (conditions: EventPredicate[]) => void;
  paths: string[];
  required: boolean;
}) {
  return <fieldset className="space-y-3">
    <legend className="mb-2 text-sm font-medium">匹配条件{required ? "（至少一项）" : "（可选）"}</legend>
    {conditions.map((condition, index) => {
      const update = (patch: Partial<EventPredicate>) => onChange(conditions.map((item, i) => i === index ? { ...item, ...patch } : item));
      return <div key={index} className="grid gap-2 rounded-lg border p-3">
        <Label htmlFor={`condition-path-${index}`}>条件 {index + 1} 的字段</Label>
        <Input id={`condition-path-${index}`} required value={condition.path} list="schedule-condition-paths" onChange={event => update({ path: event.target.value })} placeholder="例如 payload.score" />
        <Label htmlFor={`condition-op-${index}`}>比较方式</Label>
        <select id={`condition-op-${index}`} className="h-9 rounded-md border bg-background px-2 text-sm" value={condition.op} onChange={event => update({ op: event.target.value as EventPredicate["op"] })}>
          {Object.entries(operators).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
        {condition.op !== "exists" && <ConditionValue id={`condition-value-${index}`} value={condition.value} list={condition.op === "in"} onChange={value => update({ value })} />}
        <Button size="sm" variant="ghost" type="button" className="justify-self-end" onClick={() => onChange(conditions.filter((_, i) => i !== index))}>移除条件 {index + 1}</Button>
      </div>;
    })}
    <datalist id="schedule-condition-paths">{paths.map(path => <option key={path} value={path} />)}</datalist>
    <Button type="button" variant="outline" size="sm" disabled={conditions.length >= 20} onClick={() => onChange([...conditions, { path: "", op: "eq", value: "" }])}>添加条件</Button>
  </fieldset>;
}

function ConditionValue({ id, value, list, onChange }: { id: string; value: unknown; list: boolean; onChange: (value: unknown) => void }) {
  if (list) return <>
    <Label htmlFor={id}>允许的值（每行一项）</Label>
    <Textarea key={id} id={id} defaultValue={Array.isArray(value) ? value.map(item => typeof item === "string" ? item : JSON.stringify(item)).join("\n") : ""} onChange={event => {
      onChange(event.target.value.split("\n").map(item => item.trim()).filter(Boolean).map(item => {
        try { return JSON.parse(item); } catch { return item; }
      }));
    }} />
  </>;
  const kind = typeof value === "number" ? "number" : typeof value === "boolean" ? "boolean" : value === null ? "null" : typeof value === "object" ? "structured" : "string";
  return <>
    <Label htmlFor={`${id}-kind`}>值类型</Label>
    <select id={`${id}-kind`} className="h-9 rounded-md border bg-background px-2 text-sm" value={kind} onChange={event => onChange({ number: 0, boolean: true, null: null, string: "" }[event.target.value])}>
      <option value="string">文本</option><option value="number">数字</option><option value="boolean">是 / 否</option><option value="null">空值</option>
      {kind === "structured" && <option value="structured">已保存的结构化值</option>}
    </select>
    {kind === "boolean" ? <select aria-label="条件值" className="h-9 rounded-md border bg-background px-2 text-sm" value={String(value)} onChange={event => onChange(event.target.value === "true")}><option value="true">是</option><option value="false">否</option></select> :
      kind === "structured" ? <Input aria-label="已保存的条件值" readOnly value={JSON.stringify(value)} /> :
      kind !== "null" && <Input id={id} aria-label="条件值" type={kind === "number" ? "number" : "text"} step={kind === "number" ? "any" : undefined} value={String(value ?? "")} onChange={event => onChange(kind === "number" ? Number(event.target.value) : event.target.value)} />}
  </>;
}
