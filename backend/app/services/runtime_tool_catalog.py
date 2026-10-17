"""Compact default surface; legacy/specialist tools remain discoverable and callable."""

SANDBOX_CORE = frozenset({
    "sandbox_env_info", "sandbox_exec", "sandbox_get_job", "sandbox_cancel_job",
    "sandbox_read_result", "sandbox_read_file", "sandbox_write_file", "sandbox_edit_file",
    "sandbox_list_files", "sandbox_grep", "sandbox_apply_patch", "sandbox_publish_file",
    "sandbox_publish_image", "sandbox_validate_web_app", "sandbox_validate_interaction_contract",
    "sandbox_publish_web_app",
})
LEGACY_NAMES = frozenset({"sandbox_bash", "sandbox_pipeline", "sandbox_append_file",
                         "sandbox_search_web", "sandbox_fetch", "sandbox_git_clone", "lg_usage_summary"})


def keywords(name):
    groups = {"provider": "服务商 连接 模型", "model": "模型 能力 发现", "balance": "余额 账户 查询",
              "budget": "预算 限额", "usage": "用量 计费 费用", "price": "价格 计费", "email": "邮件 告警",
              "alert": "告警 通知", "setting": "设置 配置", "memory": "记忆 向量 索引",
              "plugin": "插件 扩展", "skill": "技能 扩展", "subagent": "子代理 委派 并行",
              "notebook": "持久 Python 计算 内核", "git": "代码 仓库 版本", "secret": "密钥 凭据"}
    return [text for token, text in groups.items() if token in name]


def compact_definitions(definitions, *, management_names, active_ids, active_families, progressive):
    deferred = set(management_names) | set(LEGACY_NAMES)
    deferred.update(d["function"]["name"] for d in definitions
                    if d["function"]["name"].startswith("sandbox_") and d["function"]["name"] not in SANDBOX_CORE)
    descriptors = []
    visible = []
    for definition in definitions:
        function = definition["function"]
        name = function["name"]
        if name in deferred:
            descriptors.append({
                "capability_id": f"runtime:{name}", "name": name, "function_name": name,
                "title": name, "summary": function.get("description", "")[:300],
                "family": "runtime", "kind": "builtin_tool", "authorized": True,
                "activation_required": True,
                "keywords": keywords(name),
            })
        if not progressive or name not in deferred or f"runtime:{name}" in active_ids or "runtime" in active_families:
            visible.append(definition)
    return visible, descriptors
