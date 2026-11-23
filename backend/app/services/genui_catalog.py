"""Prompt metadata and registered, bounded local catalog functions & @checks validation."""
from __future__ import annotations

import math
from typing import Any, Callable, get_args

from app.domain.schemas.stream_ui import LegacyRailIcon, resolve_data_path

ICON_GUIDANCE = (
    "图标可选，按语义少量使用，不要求每次回答都带图标。icon 使用 fluent:<slug>（Microsoft Fluent Emoji 3D），"
    "例如 fluent:rocket、fluent:light-bulb、fluent:brain；常用slug还有 books、direct-hit、check-mark-button、warning、sparkles、"
    "magnifying-glass-tilted-left、chart-increasing。slug只含ASCII小写字母、数字和单个连字符分隔的非空段，整个标识最多100字符；"
    "不传URL、路径、SVG、样式或原始emoji到icon字段。兼容旧Lucide白名单：" + ",".join(get_args(LegacyRailIcon)) + "。"
    "普通document节点均可加icon，例如heading、section、rail_item；独立图标节点用type=icon并必须提供icon，不能有子节点。"
    "组件标题可用props.icon，metric/timeline条目可用items[].icon（嵌入visual也适用）；item_status仍仅限rail_item。"
    "图标使用前端同源内置资源，不需要联网、图片搜索或image节点。普通Markdown内联emoji直接写正常Unicode，如🚀、💡、🧠，"
    "前端会转换已识别的emoji；不要在普通Markdown正文写fluent:slug代替emoji。"
)


COMPONENT_CATALOG = (
    {"name": "MetricGrid", "component": "metric", "description": "明确指标与单位", "whenToUse": "多个可核对的数值", "whenNotToUse": "普通解释或没有数据", "examples": ["月活与增长率", "不要将一句结论包装为指标"]},
    {"name": "CompareTable", "component": "table", "description": "小表格默认静态展示，数据探索时可筛选排序", "whenToUse": "多个对象共享比较维度", "whenNotToUse": "一句话即可回答", "examples": ["BM25与向量检索", "不要强迫所有对比使用表格"]},
    {"name": "LineChart", "component": "chart", "chart_type": "line", "description": "时间序列趋势", "whenToUse": "已有时间标签和真实数值", "whenNotToUse": "缺少测量数据", "examples": ["六个月用户数", "不要补齐未知月份"]},
    {"name": "BarChart", "component": "chart", "chart_type": "bar", "description": "分类数值比较", "whenToUse": "不同类别的可比数量", "whenNotToUse": "只有定性描述", "examples": ["各渠道获客数", "不要伪造实测评分"]},
    {"name": "Timeline", "component": "timeline", "description": "事件与阶段", "whenToUse": "有明确顺序或时间", "whenNotToUse": "无顺序的知识点", "examples": ["学习计划", "不要虚构发布日期"]},
    {"name": "FlowDiagram", "component": "graph", "description": "有方向的节点关系", "whenToUse": "步骤依赖或实体关系", "whenNotToUse": "简单枚举", "examples": ["检索处理链", "关系不自动构成因果"]},
    {"name": "MindMap", "component": "graph", "graph_layout": "mindmap", "description": "中心主题和自动平衡的知识分支，支持画布缩放拖动", "whenToUse": "思维导图、主题拆解或知识结构", "whenNotToUse": "强调时序与因果的流程", "examples": ["围绕机器学习拆解知识体系"]},
    {"name": "Document", "component": "document", "description": "正文、章节、引用与注册交互", "whenToUse": "长文或图文交错", "whenNotToUse": "简答", "examples": ["研究报告", "1+1只回答2"]},
    {"name": "IconFeatureList", "component": "document", "node_type": "feature_list", "description": "左侧小图标、右侧标题与解释的无边框列表", "whenToUse": "2至6个并列概念、特性或观点", "whenNotToUse": "单段简答、有明确顺序、以数值为主或比较矩阵更清楚", "examples": ["极速模式与Agent模式的适用场景", "不要随机加图标或为正文套卡片"]},
    {"name": "StepList", "component": "document", "node_type": "step_list", "description": "编号引导的操作步骤", "whenToUse": "有先后顺序的教程或路线", "whenNotToUse": "并列观点或真实日期事件", "examples": ["配置、验证、使用", "有真实日期时优先Timeline"]},
    {"name": "StatusList", "component": "document", "node_type": "status_list", "description": "有语义状态图标的结果列表", "whenToUse": "已经提供的执行状态或结果", "whenNotToUse": "计划、推测或未验证的执行结果", "examples": ["工具已返回的完成与失败状态", "不要声称尚未运行的步骤已完成"]},
)


def presentation_catalog_prompt() -> str:
    return (
        "\n内容优先，按信息增益决定视觉预算：简答通常最多一个特殊UI，纯文字始终合法，复杂任务按内容分节。"
        "不为普通正文套卡片，不重复相同信息，不增加布局模型调用。颜色、字体与像素由前端控制。"
        "props可用density=compact|comfortable|editorial、width=reading|full、toc=true|false。"
        "节点可用weight=1..6控制同行相对宽比；图片/figure节点可用image_variant=default|hero|thumbnail|compact|wide和fit=contain|cover|scale-down。"
        "思维导图用graph组件或document的diagram节点，visual.props.graph_layout=mindmap，主题和分支用nodes/edges声明，前端自动布局。画布底色用props.background=auto|paper|warm|cool|dark预设。"
        "长文用章节与toc，metric也可作为document中的visual节点。"
        "采用Editorial Data UI：指标、列表、步骤和小表格融入正文，图表突出关键数值与趋势；只有复杂分支或拓扑使用Canvas。"
        "props.interaction=auto|static|explore，默认auto。8行以内表格静态呈现，需要明确筛选排序时才用explore；static图表不显示探索操作。"
        "6节点以内的简单有向链自动使用紧凑流程，步骤与流程图表达同一内容时只选择一种，通常优先默认可读的StepList。"
        "不堆叠功能或生成语义重复的组件；不能因压缩布局丢失来源、关系或必要说明。metric首项为核心指标，次项为次要指标，其余为背景信息。"
        "编辑式列表直接排在正文中，不要再包卡片。feature_list/step_list/status_list为父节点，其子节点只能是rail_item，用parent_id关联。"
        "rail_item的label是标题、text是Markdown说明、caption是补充说明；feature_list条目可选icon，status_list条目用item_status=pending|active|completed|error。"
        "每组最多12项，通常2至6项。先提交列表父节点，再按阅读顺序增加条目；可用相同id补充text/caption，避免改写已完成的条目。"
        + ICON_GUIDANCE +
        "来源写source_note，单位写unit，时间范围写time_range；示例必须data_kind=example，不可当作实测。"
        "有已保存工具结果part_id时，可用data_ref={part_id,path:[字段名或数组索引]}引用其中的rows/items格式集合。"
        "路径从工具output开始，只能引用同一会话内已完成的工具结果，服务端覆盖该集合；未知part_id不要猜测。"
        "通用数据集可额外声明label_key、id_key和value_keys={列key:源字段名}，由服务端确定性映射为chart/table行，不执行表达式。"
        "结构与数据分离时，可在data_model放置结构化数据并用@/path（或文本中@{/path}）绑定，后续仅用op=data更新data_model。"
        "支持本地安全校验规则checks=[{id,fn,path,args,message,severity}]（如required、min_value、max_value、range、one_of等），不执行表达式。"
        "不适合此结构时使用已获得的数据构造组件并说明来源，不虚构来源ID。"
        "数值预览在完整校验前保持占位；先写解释并建立稳定id，不等待图表才继续文字。"
        "Agent模式下用户要求修改先前图表时，可用op=revise、原block_id和最后base_revision，"
        "仅提交需要改变的props/upsert/delete，然后complete。新回答保存该块的修订版，保留原回答，"
        "不重写整篇正文。revise不带component，只能修改同一会话已完成的块。"
        "原生文档已承载完整报告时，最终回复只引用该文档并给必要的简短过渡，不再逐项复述指标、计算、步骤或排版设计。"
        "以下名称仅用于选择表达方式，不能复制为节点字段；文档节点没有component或node_type字段：\n"
        + "\n".join(
            f"- {entry['name']}：" + (
                f"文档节点 type={entry['node_type']}，子节点 type=rail_item（用parent_id关联）"
                if entry.get("node_type") else f"独立块 component={entry['component']}"
            ) + f"；适用：{entry['whenToUse']}；避免：{entry['whenNotToUse']}。"
            for entry in COMPONENT_CATALOG
        ) + "\n"
    )


def _is_finite_num(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _fn_required(value: object, *_args: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


def _fn_non_empty(value: object, *_args: object) -> bool:
    if isinstance(value, str):
        return len(value.strip()) > 0
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return value is not None


def _fn_min_length(value: object, limit: object, *_args: object) -> bool:
    if not _is_finite_num(limit) or (isinstance(limit, float) and not limit.is_integer()):
        return False
    n = int(limit)  # type: ignore[arg-type]
    if isinstance(value, (str, list, dict)):
        return len(value) >= n
    return False


def _fn_max_length(value: object, limit: object, *_args: object) -> bool:
    if not _is_finite_num(limit) or (isinstance(limit, float) and not limit.is_integer()):
        return False
    n = int(limit)  # type: ignore[arg-type]
    if isinstance(value, (str, list, dict)):
        return len(value) <= n
    return False


def _fn_min_value(value: object, minimum: object, *_args: object) -> bool:
    return _is_finite_num(value) and _is_finite_num(minimum) and float(value) >= float(minimum)  # type: ignore[arg-type]


def _fn_max_value(value: object, maximum: object, *_args: object) -> bool:
    return _is_finite_num(value) and _is_finite_num(maximum) and float(value) <= float(maximum)  # type: ignore[arg-type]


def _fn_range(value: object, minimum: object, maximum: object, *_args: object) -> bool:
    return (
        _is_finite_num(value)
        and _is_finite_num(minimum)
        and _is_finite_num(maximum)
        and float(minimum) <= float(value) <= float(maximum)  # type: ignore[arg-type]
    )


def _fn_one_of(value: object, *options: object) -> bool:
    return any(type(value) is type(opt) and value == opt for opt in options)


def _fn_gt(value: object, target: object, *_args: object) -> bool:
    return _is_finite_num(value) and _is_finite_num(target) and float(value) > float(target)  # type: ignore[arg-type]


def _fn_gte(value: object, target: object, *_args: object) -> bool:
    return _fn_min_value(value, target)


def _fn_lt(value: object, target: object, *_args: object) -> bool:
    return _is_finite_num(value) and _is_finite_num(target) and float(value) < float(target)  # type: ignore[arg-type]


def _fn_lte(value: object, target: object, *_args: object) -> bool:
    return _fn_max_value(value, target)


def _fn_eq(value: object, target: object, *_args: object) -> bool:
    if _is_finite_num(value) and _is_finite_num(target):
        return float(value) == float(target)  # type: ignore[arg-type]
    return type(value) is type(target) and value == target


def _fn_ne(value: object, target: object, *_args: object) -> bool:
    return not _fn_eq(value, target)


def _fn_is_finite_number(value: object, *_args: object) -> bool:
    return _is_finite_num(value)


CATALOG_CHECK_FUNCTIONS: dict[str, Callable[..., bool]] = {
    "required": _fn_required,
    "non_empty": _fn_non_empty,
    "min_length": _fn_min_length,
    "max_length": _fn_max_length,
    "min_value": _fn_min_value,
    "max_value": _fn_max_value,
    "range": _fn_range,
    "one_of": _fn_one_of,
    "gt": _fn_gt,
    "gte": _fn_gte,
    "lt": _fn_lt,
    "lte": _fn_lte,
    "eq": _fn_eq,
    "ne": _fn_ne,
    "is_finite_number": _fn_is_finite_number,
}


def evaluate_check(rule: dict[str, Any], data_model: dict[str, object]) -> dict[str, Any]:
    fn_name = rule.get("fn", "")
    fn = CATALOG_CHECK_FUNCTIONS.get(fn_name)
    if fn is None:
        raise ValueError(f"Unknown catalog check function: {fn_name!r}")
    found, value = resolve_data_path(data_model, str(rule.get("path") or ""))
    args = rule.get("args") or []
    passed = fn(value if found else None, *args)
    return {
        "id": rule.get("id"),
        "passed": bool(passed),
        "message": rule.get("message", ""),
        "severity": rule.get("severity", "warning"),
        "target_id": rule.get("target_id"),
    }


def evaluate_checks(checks: list[dict[str, Any]], data_model: dict[str, object]) -> list[dict[str, Any]]:
    return [evaluate_check(rule, data_model) for rule in checks]
