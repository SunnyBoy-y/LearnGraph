import { useCallback, useMemo, type SetStateAction } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { workspaceQueryKey } from '@/lib/query-keys'
import type { Message } from '@/types/sessions'

const emptyMessages: Message[] = []

/** In-memory stream overlays outlive route outlets, but follow auth cache cleanup. */
export function useLiveChatMessages(workspaceId: string) {
  const client = useQueryClient()
  const key = useMemo(() => workspaceQueryKey(workspaceId, 'live-chat-messages'), [workspaceId])
  const { data = emptyMessages } = useQuery<Message[]>({
    queryKey: key,
    queryFn: () => emptyMessages,
    initialData: emptyMessages,
    enabled: false,
    gcTime: Infinity,
  })
  const setMessages = useCallback((update: SetStateAction<Message[]>) => {
    client.setQueryData<Message[]>(key, current =>
      typeof update === 'function' ? update(current ?? emptyMessages) : update,
    )
  }, [client, key])
  return [data, setMessages] as const
}
