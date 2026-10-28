/** Audited against the existing forms and API payloads. Metadata never changes persistence keys. */
export type SettingScope = 'workspace' | 'session' | 'account' | 'device' | 'organization' | 'deployment'
export type SettingsCategory = 'general' | 'conversation' | 'memory' | 'voice' | 'models' | 'tools' | 'workspace' | 'account' | 'data' | 'about'
export type SettingMetadata = {
  settingId: string
  title: string
  category: SettingsCategory
  section: string
  keywords: string[]
  scope: SettingScope
  previousPage: string
  field: string
  save: 'immediate' | 'explicit' | 'operation' | 'read-only'
  dependency?: string
}

function entry(settingId: string, title: string, category: SettingsCategory, section: string, scope: SettingScope,
  previousPage: string, field = settingId, save: SettingMetadata['save'] = 'immediate', dependency?: string): SettingMetadata {
  return { settingId, title, category, section, scope, previousPage, field, save, dependency, keywords: [title, section, field, previousPage] }
}

export const settingsCatalog: SettingMetadata[] = [
  entry('stickers', '表情包、关键词检索与自动语义索引', 'conversation', '表情包', 'workspace', '对话与学习', '/stickers/settings'),
  entry('stickers.import', '导入私有表情包与编辑中文描述', 'conversation', '表情包', 'account', '对话与学习', '/stickers/import', 'operation'),
  entry('stickers.market', '表情市场、授权素材预览安装与 ZIP 下载', 'conversation', '表情包', 'account', '对话与学习', '/stickers/market', 'operation'),
  entry('stickers.pipeline', '视觉模型自动分析表情配字、情绪与适用场景', 'conversation', '表情包', 'account', '对话与学习', '/stickers/pipeline'),
  entry('ui.preferences', '显示模式与主题配色', 'general', '外观', 'workspace', '工作区设置', 'ui.preferences.theme / colorTheme / customColor'),
  ...['base_style', 'warmth', 'enthusiasm', 'headings_and_lists', 'emoji', 'verbosity'].map((key, i) =>
    entry(`response-style.${key}`, ['基础风格', '温和体贴', '热情洋溢', '标题和列表', '表情符号', '回答详细度'][i],
      'general', '回答风格', 'workspace', '个性化', `chat.response_style.${key}`, 'explicit')),
  entry('chat.default_response_mode', '默认响应模式', 'conversation', '默认行为', 'workspace', '工作区设置'),
  entry('chat.suggested_prompts', '生成下一步问题提示', 'conversation', '默认行为', 'workspace', '工作区设置'),
  entry('chat.thinking_chain_default', '思考过程默认显示状态', 'conversation', '默认行为', 'workspace', '工作区设置'),
  entry('summarization.enabled', '自动压缩长对话（会话摘要）', 'conversation', '上下文管理', 'workspace', '工作区设置', '/memory/enhancement: summarization.enabled'),
  entry('chat.context_usage', '上下文用量显示', 'conversation', '上下文管理', 'workspace', '工作区设置'),
  entry('stream-stats', '显示流式指标', 'conversation', '高级诊断', 'device', '工作区设置', 'learngraph.showStreamStats'),
  entry('cache-hit', '显示缓存命中', 'conversation', '高级诊断', 'device', '工作区设置', 'learngraph.showCacheHit'),
  entry('workspace_enabled', '工作区共同记忆', 'memory', '记忆策略', 'workspace', '工作区设置', '/memory/policy: workspace_enabled'),
  entry('workspace_recall_enabled', '在回答中读取已有记忆', 'memory', '记忆策略', 'workspace', '工作区设置 / 记忆弹窗', '/memory/policy: workspace_recall_enabled', 'immediate', 'workspace_enabled'),
  entry('workspace_learning_enabled', '从新对话中整理记忆', 'memory', '记忆策略', 'workspace', '工作区设置 / 记忆弹窗', '/memory/policy: workspace_learning_enabled', 'immediate', 'workspace_enabled'),
  entry('session_enabled', '会话共同记忆', 'memory', '会话策略', 'session', '工作区设置', '/memory/policy: session_id, session_enabled', 'immediate', 'workspace_enabled'),
  entry('session_recall_enabled', '会话读取已有记忆', 'memory', '会话策略', 'session', '工作区设置', '/memory/policy: session_id, session_recall_enabled', 'immediate', 'workspace_enabled, workspace_recall_enabled, session_enabled'),
  entry('session_learning_enabled', '会话贡献新的记忆', 'memory', '会话策略', 'session', '工作区设置', '/memory/policy: session_id, session_learning_enabled', 'immediate', 'workspace_enabled, workspace_learning_enabled, session_enabled'),
  entry('effective_enabled', '会话当前有效状态', 'memory', '会话策略', 'session', '工作区设置', '/memory/policy: effective_enabled', 'read-only'),
  entry('extraction.enabled', '记忆整理', 'memory', '记忆整理与写入', 'workspace', '工作区设置 / 记忆弹窗', '/memory/enhancement: extraction.enabled', 'immediate', 'workspace_enabled, workspace_learning_enabled'),
  entry('extraction.auto_commit', '高置信新记忆自动写入', 'memory', '记忆整理与写入', 'workspace', '工作区设置 → 功能模型', '/memory/enhancement: extraction.auto_commit', 'immediate', 'workspace_enabled, workspace_learning_enabled, extraction.enabled'),
  entry('embedding.enabled', '语义检索', 'memory', '检索配置', 'workspace', '工作区设置', '/memory/enhancement: embedding.enabled', 'immediate', '启用的 Provider 与 Embedding 模型；未配置时保持原有默认召回'),
  entry('memory.index-status', '活跃记忆、索引数量与模型缓存状态', 'memory', '记忆总览 / 索引管理', 'workspace', '工作区设置', '/memory/enhancement', 'read-only'),
  entry('memory.reindex', '重建索引（可同时清理旧缓存）', 'memory', '索引管理', 'workspace', '工作区设置', '/memory/enhancement/reindex', 'operation'),
  entry('memory.prune', '清理旧模型向量缓存', 'memory', '索引管理', 'workspace', '工作区设置', '/memory/enhancement/prune-embeddings', 'operation'),
  entry('memory.management', '记忆查看、恢复、治理与导出', 'memory', '记忆管理', 'workspace', '工作区设置 → 危险区 / 记忆页', '/memory', 'operation'),
  entry('memory.diagnostics', '受众、检索作用域、架构状态、事件导出与重放校验', 'memory', '高级检索设置', 'workspace', '记忆弹窗 → 高级', '/memory architecture / event APIs', 'operation'),
  entry('chat.asr_language', 'ASR 识别语言', 'voice', '语音输入', 'workspace', '工作区设置'),
  entry('chat.dictation_cleanup', '语音转写智能整理', 'voice', '语音输入', 'workspace', '工作区设置'),
  entry('chat.asr_hotwords', '热词表', 'voice', '语音输入', 'workspace', '工作区设置'),
  entry('chat.asr_vad_mode', '本机与云端 VAD 分工', 'voice', '端点检测', 'workspace', '工作区设置'),
  entry('chat.asr_vad_adaptive', '自适应底噪基线', 'voice', '高级语音处理', 'workspace', '工作区设置'),
  entry('voice.debug_panel', '语音调试面板', 'voice', '高级诊断', 'workspace', '个性化'),
  entry('voice.relay', '中继模式、STUN、TURN、TTL、凭据及连接测试', 'voice', '语音中继', 'deployment', '语音中继', '/voice/relay', 'explicit', '系统管理员；保留原有启停、删除确认与脱敏'),
  entry('providers', '连接搜索、能力筛选、导入、添加与模型发现', 'models', '服务连接', 'workspace', '模型与服务', 'provider APIs', 'operation'),
  entry('provider.configuration', '连接地址、密钥、Headers、模型、能力探测、余额、优先级、启停与删除', 'models', '服务连接 → 连接详情', 'workspace', '模型与服务', 'provider APIs', 'explicit'),
  ...[
    ['chat.auto_title_model', '生成标题模型'], ['chat.suggested_prompts_model', '生成下一步问题提示模型'],
    ['chat.dictation_cleanup_model', '语音转写整理模型'], ['practice.exercise_model', '练习出题与判分模型'],
    ['learning.package_model', '教学包生成模型'],
  ].map(([key, title]) => entry(key, title, 'models', '任务模型分配', 'workspace', '工作区设置 → 功能模型')),
  entry('memory.model', '记忆整理与会话摘要模型', 'models', '任务模型分配', 'workspace', '工作区设置 / 记忆弹窗', '/memory/enhancement: extraction + summarization provider_id, model_id, follow_conversation'),
  entry('embedding.model', 'Embedding Provider、已发现模型与手动模型 ID', 'models', '任务模型分配', 'workspace', '工作区设置 / 记忆高级设置', '/memory/enhancement: embedding.provider_id, embedding.model_id', 'immediate', '切换已有索引模型前确认；索引管理链接到记忆与检索'),
  entry('voice-asr-binding', '实时语音识别的专用服务与模型绑定', 'models', '服务连接 → 语音转写', 'workspace', '模型与服务', 'models.functional_defaults.realtime_transcription', 'explicit'),
  entry('usage', '原币种用量、账单、导出、过滤与清理', 'models', '用量与预算', 'workspace', '用量与预算', 'usage APIs', 'operation'),
  entry('budget', '预算与限额', 'models', '用量与预算', 'workspace', '用量与预算', 'budget APIs', 'explicit'),
  entry('extensions', '插件、Skills、MCP、可信组件及版本管理', 'tools', '已安装工具与扩展', 'workspace', '扩展中心', 'existing extension APIs', 'operation'),
  entry('sandbox', '沙箱运行时、预览、镜像来源与初始化', 'tools', '扩展 → 沙箱', 'deployment', '扩展中心', 'sandbox bootstrap / preview APIs', 'explicit', '保持系统管理员与工作区管理者的原有权限判断'),
  entry('egress', '沙箱联网、工作区允许列表、审批与审计', 'tools', '搜索与网页抓取', 'workspace', '搜索与抓取', 'egress APIs', 'explicit'),
  entry('personal-allowlist', '我的个人白名单', 'tools', '搜索与网页抓取', 'account', '搜索与抓取', 'personal fetch allowlist', 'explicit'),
  entry('fetch-channel', '网页抓取通道', 'tools', '网页抓取', 'workspace', '搜索与抓取', 'fetch channel API'),
  entry('workspace-info', '工作区名称、类型、角色及 ID', 'workspace', '工作区概览', 'workspace', '工作区设置', '/workspaces', 'read-only'),
  entry('members', '组织、成员、角色、系统用户目录与访问权限', 'workspace', '成员与访问', 'organization', '账户与访问', 'existing control APIs', 'explicit'),
  entry('sharing', '会话分享及共享访问管理', 'workspace', '会话与分享', 'session', '会话与分享', 'existing session share APIs', 'operation'),
  entry('audit', '运行与权限审计（同一份日志）', 'workspace', '权限审计', 'workspace', '权限审计', 'audit APIs', 'operation'),
  entry('profile', '头像、昵称、用户名与用户 ID', 'account', '个人资料', 'account', '工作区设置', 'auth profile APIs', 'explicit'),
  entry('password', '修改密码', 'account', '密码与登录', 'account', '工作区设置', 'auth change-password API', 'explicit'),
  entry('auth-sessions', '登录设备及会话下线', 'account', '账户安全', 'account', '账户与访问', 'auth sessions APIs', 'operation'),
  entry('account-deletion', '删除我的账户', 'account', '危险操作', 'account', '工作区设置 → 危险区', 'deletion impact + delete account APIs', 'operation', '保留影响预览、用户名确认、二次确认与服务端鉴权'),
  entry('selection-history', '清除本设备学习痕迹', 'data', '本地数据', 'device', '个性化', 'clearSelectionExplanations（当前账户与工作区分区）', 'operation'),
  entry('storage', '数据库、文件存储、适配器状态与配置', 'data', '存储与数据迁移', 'deployment', '存储迁移', 'existing storage configuration APIs', 'explicit'),
  entry('migration', '备份、恢复、预检、迁移、提交、回滚与历史', 'data', '存储与数据迁移', 'deployment', '存储迁移', 'existing migration APIs', 'operation'),
  entry('updates', '版本更新、自动更新策略与回滚', 'about', '版本与更新', 'deployment', '版本与更新', 'existing update APIs', 'operation', '系统管理员'),
  entry('about', '版本与关于信息', 'about', '关于', 'device', '关于', 'existing about view', 'read-only'),
]
