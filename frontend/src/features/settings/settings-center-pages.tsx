import { lazy } from 'react'
import { PageFrame, PageIntro } from '@/components/shared/page-elements'
import { SettingsTabs } from './settings-tabs'
import { SettingsSection } from './settings-primitives'
import { VoiceInputSettings } from './voice-input-settings'
import { VoiceDebugSettings } from './voice-debug-settings'
import { WorkspaceOverview } from './workspace-overview'
import { TaskModelSettings } from './task-model-settings'
import { LocalDataSettings } from './local-data-settings'
import { SiteBackupPanel } from './site-backup-panel'

const AboutPage = lazy(() => import('./about-page').then(m => ({ default: m.AboutPage })))
const UpdatesPage = lazy(() => import('./updates-page').then(m => ({ default: m.UpdatesPage })))
const ProvidersPage = lazy(() => import('./provider-pages').then(m => ({ default: m.ProvidersPage })))
const UsagePage = lazy(() => import('./usage-pages').then(m => ({ default: m.UsagePage })))
const VoiceRelayPage = lazy(() => import('./voice-relay-page').then(m => ({ default: m.VoiceRelayPage })))
const ExtensionsPage = lazy(() => import('./extension-pages').then(m => ({ default: m.ExtensionsPage })))
const SearchFetchPage = lazy(() => import('./search-fetch-page').then(m => ({ default: m.SearchFetchPage })))
const AccessManagementPage = lazy(() => import('./control-pages').then(m => ({ default: m.AccessManagementPage })))
const SessionSharingPage = lazy(() => import('./session-sharing-page').then(m => ({ default: m.SessionSharingPage })))
const AuditPage = lazy(() => import('./governance-pages').then(m => ({ default: m.AuditPage })))
const MigrationPage = lazy(() => import('./governance-pages').then(m => ({ default: m.MigrationPage })))

export function ModelSettingsPage() {
  return <PageFrame><PageIntro title="模型与服务" description="管理服务连接、任务模型与用量预算。"/>
    <SettingsTabs items={[
      { id: 'connections', label: '服务连接', content: <ProvidersPage/> },
      { id: 'tasks', label: '任务模型分配', content: <TaskModelSettings/> },
      { id: 'usage', label: '用量与预算', content: <UsagePage/> },
    ]}/></PageFrame>
}

export function VoiceSettingsPage() {
  return <PageFrame><PageIntro title="语音与音频" description="管理语音识别、音频处理与中继连接。"/>
    <SettingsTabs items={[
      { id: 'input', label: '语音输入', content: <VoiceInputSettings/> },
      { id: 'relay', label: '语音中继', content: <SettingsSection title="语音中继" description="全系统配置，仅系统管理员可修改。"><VoiceRelayPage/></SettingsSection> },
      { id: 'diagnostics', label: '高级诊断', content: <VoiceDebugSettings/> },
    ]}/></PageFrame>
}

export function ToolsSettingsPage() {
  return <PageFrame><PageIntro title="工具与扩展" description="管理扩展、搜索与网页抓取能力。"/>
    <SettingsTabs items={[
      { id: 'extensions', label: '已安装工具与扩展', content: <ExtensionsPage/> },
      { id: 'search', label: '搜索与网页抓取', content: <SearchFetchPage/> },
    ]}/></PageFrame>
}

export function WorkspaceSettingsPage() {
  return <PageFrame><PageIntro title="工作区与协作" description="查看工作区资料，管理成员、分享与权限审计。"/>
    <SettingsTabs items={[
      { id: 'overview', label: '工作区概览', content: <WorkspaceOverview/> },
      { id: 'members', label: '成员与访问', content: <AccessManagementPage section="collaboration"/> },
      { id: 'sharing', label: '会话与分享', content: <SessionSharingPage/> },
      { id: 'audit', label: '权限审计', content: <AuditPage/> },
    ]}/></PageFrame>
}

export function DataSettingsPage() {
  return <PageFrame><PageIntro title="数据与存储" description="清理本设备数据，管理存储与迁移。"/>
    <SettingsTabs items={[
      { id: 'local', label: '本地数据', content: <LocalDataSettings/> },
      { id: 'backup', label: '全站备份与恢复', content: <SiteBackupPanel/> },
      { id: 'migration', label: '存储与数据迁移', content: <MigrationPage/> },
    ]}/></PageFrame>
}

export function AboutSettingsPage() { return <PageFrame><PageIntro title="关于" description="查看 LearnGraph 信息与版本更新。"/><SettingsTabs items={[{id:'about',label:'关于 LearnGraph',content:<AboutPage/>},{id:'updates',label:'版本与更新',content:<UpdatesPage/>}]}/></PageFrame> }
