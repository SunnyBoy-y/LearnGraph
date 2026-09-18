CHAT_SUGGESTED_PROMPTS_SETTING_KEY = "chat.suggested_prompts"
CHAT_RESPONSE_STYLE_SETTING_KEY = "chat.response_style"
CHAT_DEFAULT_RESPONSE_MODE_SETTING_KEY = "chat.default_response_mode"
CHAT_AUTO_TITLE_MODEL_SETTING_KEY = "chat.auto_title_model"
CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY = "chat.suggested_prompts_model"
CHAT_DICTATION_CLEANUP_SETTING_KEY = "chat.dictation_cleanup"
CHAT_DICTATION_CLEANUP_MODEL_SETTING_KEY = "chat.dictation_cleanup_model"
CHAT_CONTEXT_USAGE_SETTING_KEY = "chat.context_usage"
# 复习与练习中心：出题与简答题判分共用的模型。与 chat.*_model 同形（provider_id
# + model_id 成对或同时为 null），未配置时回落到工作区对话模型。
PRACTICE_EXERCISE_MODEL_SETTING_KEY = "practice.exercise_model"
# 教学包（节点学习页）生成模型：教材 / 互动实验 / 小剧场 / 闯关测评共用同一个模型。
# 与 chat.*_model 同形（provider_id + model_id 成对或同时为 null），未配置时回落到
# 工作区对话模型。长结构化输出建议指向输出上限更大的模型。
LEARNING_PACKAGE_MODEL_SETTING_KEY = "learning.package_model"
# 图谱书架的「AI 生成封面」：svg 引擎（文本模型直接产出矢量图）用的模型，与
# chat.*_model 同形（provider_id + model_id 成对或同时为 null），未配置时回落到
# 工作区对话模型。image 引擎不走这个键，它用 image_generation 的功能模型默认值。
GRAPH_COVER_MODEL_SETTING_KEY = "graph.cover_model"
# 「AI 生成封面」的默认引擎：svg（LLM 直接画矢量图，不产生图片模型费用）或
# image（生图模型出位图）。取值非法或未配置时一律按 svg 处理。
GRAPH_COVER_ENGINE_SETTING_KEY = "graph.cover_engine"
FUNCTIONAL_MODEL_DEFAULTS_SETTING_KEY = "models.functional_defaults"
TRAJECTORY_ENABLED_SETTING_KEY = "trajectory.enabled"
