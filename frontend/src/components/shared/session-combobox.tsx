import { useState } from 'react'
import { ChevronsUpDown } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'
import type { Session } from '@/types/sessions'

/** Searchable session picker: fuzzy title filtering plus a scrollable list. */
export function SessionCombobox({
  sessions,
  value,
  onChange,
  placeholder = '选择 Session',
  disabled = false,
  id,
  className,
}: {
  sessions: Session[]
  value: string
  onChange: (sessionId: string) => void
  placeholder?: string
  disabled?: boolean
  id?: string
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const selected = sessions.find((session) => session.id === value)

  return (
    <Popover onOpenChange={setOpen} open={open}>
      <PopoverTrigger asChild>
        <Button
          aria-expanded={open}
          className={cn('w-full justify-between font-normal', className)}
          disabled={disabled}
          id={id}
          role="combobox"
          variant="outline"
        >
          <span className={cn('truncate', !selected && 'text-muted-foreground')}>
            {selected ? selected.title : placeholder}
          </span>
          <ChevronsUpDown className="size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-(--radix-popover-trigger-width) p-0">
        <Command>
          <CommandInput placeholder="搜索会话标题…" />
          <CommandList className="max-h-64" style={{ scrollbarWidth: 'thin' }}>
            <CommandEmpty>没有匹配的会话</CommandEmpty>
            {sessions.map((session) => (
              <CommandItem
                data-checked={session.id === value}
                key={session.id}
                onSelect={() => {
                  onChange(session.id)
                  setOpen(false)
                }}
                // The id suffix keeps duplicate titles ("新会话") individually
                // selectable while the visible text still drives the search.
                value={`${session.title} ${session.id}`}
              >
                <span className="min-w-0 flex-1 truncate">{session.title}</span>
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
