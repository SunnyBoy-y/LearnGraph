import { useEffect, useMemo, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { authStore } from '@/api/auth-store'
import { listProviders, discoverProviderModels } from '@/api/providers'
import { listSettings } from '@/api/settings'
import { getSessionSuggestedPrompts, generateSessionSuggestedPrompts, listMessageVersions, listSessions } from '@/api/sessions'
import { FollowUpPrompts } from '@/components/chat/suggested-prompts'
import { providerModelOptions } from '@/lib/model-choices'
import { workspaceQueryKey } from '@/lib/query-keys'
import { shouldShowSuggestedPromptError } from '@/lib/suggested-prompts'
import { areChatSuggestedPromptsEnabled, CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY, readChatFeatureModelSetting } from '@/lib/workspace-settings'
import { isModelProviderType } from '@/types/providers'
import type { SuggestedPromptBatch } from '@/types/sessions'
import type { NativeSuggestionsPayload } from './protocol'

/** The same persisted-batch → exact anchor/version → count=2 generation contract as ChatCanvasPage. */
export function NativeSuggestedPrompts({ workspaceId, sessionId, anchor, native, onSelect, onConfigureProvider }: {
  workspaceId: string
  sessionId: string
  anchor?: NativeSuggestionsPayload
  native: boolean
  onSelect: (content: string) => void
  onConfigureProvider: () => void
}) {
  const authorized = !!authStore.getAccessToken() && authStore.getWorkspaceId() === workspaceId
  const enabled = native && authorized && !!anchor && !!sessionId && sessionId !== 'new'
  const settings = useQuery({ queryKey: workspaceQueryKey(workspaceId, 'settings'), queryFn: ({ signal }) => listSettings({ signal }), enabled, retry: false })
  const sessions = useQuery({ queryKey: workspaceQueryKey(workspaceId, 'sessions'), queryFn: listSessions, enabled, retry: false })
  const currentSession = sessions.data?.find(session => session.id === sessionId)
  const canPrepare = enabled && settings.isSuccess && areChatSuggestedPromptsEnabled(settings.data) &&
    sessions.isSuccess && !!currentSession && currentSession.status !== 'closed'

  const providers = useQuery({ queryKey: workspaceQueryKey(workspaceId, 'providers'), queryFn: ({ signal }) => listProviders({ signal }), enabled: canPrepare, retry: false })
  const modelProviders = useMemo(() => (providers.data ?? []).filter(provider => provider.enabled && provider.remote_capability && isModelProviderType(provider.provider_type)), [providers.data])
  const activeModelProvider = modelProviders.find(provider => provider.id === anchor?.providerId) ?? modelProviders[0]
  const discoveredModels = useQuery({ queryKey: workspaceQueryKey(workspaceId, 'provider-models', activeModelProvider?.id ?? null),
    queryFn: () => discoverProviderModels(activeModelProvider!.id), enabled: canPrepare && !!activeModelProvider, retry: false })
  const modelOptions = useMemo(() => providerModelOptions(activeModelProvider, discoveredModels.data?.models), [activeModelProvider, discoveredModels.data?.models])
  const selectedModel = modelOptions.find(model => model.id === anchor?.modelId)
  const remoteAvailable = !!activeModelProvider?.id && !!selectedModel?.id && selectedModel.remote
  const providerPending = providers.isPending || !!activeModelProvider &&
    (discoveredModels.isPending || modelOptions.length > 0 && !selectedModel)
  const providerError = providers.isError || !remoteAvailable && discoveredModels.isError
  const featureModel = readChatFeatureModelSetting(settings.data, CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY)

  const versions = useQuery({ queryKey: workspaceQueryKey(workspaceId, 'message-versions', anchor?.anchorSessionId ?? sessionId, anchor?.anchorMessageId ?? null, anchor?.anchorVersion ?? 0),
    queryFn: () => listMessageVersions(anchor!.anchorSessionId, anchor!.anchorMessageId), enabled: canPrepare, retry: false })
  const version = versions.data?.find(item => item.version === anchor?.anchorVersion && item.status === 'completed')
  const anchorError = canPrepare && (versions.isError || versions.isSuccess && !version)
  const canRead = canPrepare && !!version
  const context = [sessionId, anchor?.completedMessageFingerprint ?? '', anchor?.anchorMessageId ?? null,
    anchor?.anchorVersion ?? 0, version?.id ?? null, activeModelProvider?.id ?? null, selectedModel?.id ?? null] as const
  const persisted = useQuery({ queryKey: workspaceQueryKey(workspaceId, 'suggested-prompts', 'persisted', ...context),
    queryFn: async ({ signal }) => (await getSessionSuggestedPrompts(sessionId, { signal })) ?? null,
    enabled: canRead, retry: false })
  const matching = (batch: SuggestedPromptBatch | null | undefined) => batch?.session_id === sessionId &&
    batch.anchor_message_id === anchor?.anchorMessageId && batch.anchor_message_version_id === version?.id ? batch : undefined
  const persistedBatch = matching(persisted.data)
  const canGenerate = canRead && persisted.isSuccess && !persistedBatch && remoteAvailable
  const generated = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'suggested-prompts', 'generated', ...context, featureModel.provider_id, featureModel.model_id),
    queryFn: ({ signal }) => generateSessionSuggestedPrompts(sessionId, {
      anchor_message_id: anchor!.anchorMessageId, anchor_message_version_id: version!.id,
      provider_id: featureModel.provider_id ?? activeModelProvider!.id,
      model_id: featureModel.model_id ?? selectedModel!.id, count: 2,
    }, { signal }),
    enabled: canGenerate, retry: false, staleTime: Number.POSITIVE_INFINITY,
  })
  const prompts = (matching(generated.data) ?? persistedBatch)?.prompts ?? []
  const pending = canPrepare && (versions.isPending || versions.isFetching || canRead && (
    persisted.isPending || persisted.isFetching || !persistedBatch && !persisted.isError &&
    (providerPending || canGenerate && (generated.isPending || generated.isFetching))))
  const unavailable = canRead && persisted.isSuccess && !persistedBatch && !providerPending && !providerError && !remoteAvailable
  const failed = shouldShowSuggestedPromptError({ canPrepare, canRead, anchorError,
    persistedReadError: persisted.isError, hasPersistedBatch: !!persistedBatch,
    providerError, canGenerate, generationError: generated.isError })
  const retry = () => {
    if (anchorError) void versions.refetch()
    else if (persisted.isError) void persisted.refetch()
    else if (providers.isError) void providers.refetch()
    else if (!remoteAvailable && discoveredModels.isError) void discoveredModels.refetch()
    else void generated.refetch()
  }
  const failureKey = failed && !pending ? [sessionId, anchor?.anchorMessageId, version?.id,
    generated.isError ? 'generation' : 'read', generated.failureCount].join(':') : ''
  const noticed = useRef('')
  useEffect(() => {
    if (!failureKey) { noticed.current = ''; return }
    if (noticed.current === failureKey) return
    noticed.current = failureKey
    toast.error('问题提示生成失败', { description: '推荐问题暂时不可用，请稍后重试。', action: { label: '重试', onClick: retry } })
  })

  if (!native) return <FollowUpPrompts prompts={[{ id: 'preview-next-1', content: '能用一个具体例子解释吗？' },
    { id: 'preview-next-2', content: '接下来可以怎样练习？' }]} onSelect={onSelect} isPending={false} isUnavailable={false} onConfigureProvider={onConfigureProvider} />
  if (!canPrepare || !pending && !unavailable && !prompts.length) return null
  return <FollowUpPrompts prompts={prompts} onSelect={onSelect} isPending={pending} isUnavailable={unavailable} onConfigureProvider={onConfigureProvider} />
}
