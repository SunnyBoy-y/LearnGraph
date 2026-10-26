"""Bounded syntax compatibility for tool-less model declarations.

Only known declarative nesting aliases are translated. The canonical command
still passes the unchanged strict schema; values, document node IDs and unknown keys survive.
"""
from copy import deepcopy

VISUALS = {"chart", "metric", "table", "graph", "diagram", "timeline"}
RAILS = {"feature_list", "step_list", "status_list"}


def canonical_inline_document(raw: dict) -> dict:
    result = deepcopy(raw)
    upsert = result.get("upsert")
    if not isinstance(upsert, dict) or not isinstance(upsert.get("blocks"), list):
        return result
    if len(upsert["blocks"]) > 160:
        raise ValueError("Too many document nodes")
    blocks = []
    for node in upsert["blocks"]:
        if not isinstance(node, dict):
            blocks.append(node)
            continue
        # Some models copy catalog metadata rather than the flat-tree spelling.
        if node.get("type") == "document" and node.get("node_type") in RAILS:
            node["type"] = node.pop("node_type")
        if node.get("type") in VISUALS:
            visual = node.get("visual")
            if visual is None:
                visual = {}
            if isinstance(visual, dict):
                for key in ("props", "rows", "nodes", "edges", "items"):
                    if key in node:
                        if key in visual and visual[key] != node[key]:
                            raise ValueError("Conflicting visual fields")
                        visual[key] = node.pop(key)
                props = visual.get("props")
                if isinstance(props, dict):
                    if "columns" in visual:
                        if "columns" in props and props["columns"] != visual["columns"]:
                            raise ValueError("Conflicting column definitions")
                        props["columns"] = visual.pop("columns")
                    # Retain unsupported axis captions as ordinary description.
                    captions = []
                    for key, label in (("x_label", "横轴"), ("y_label", "纵轴")):
                        if key in props:
                            value = props.pop(key)
                            if not isinstance(value, str) or len(value) > 100:
                                raise ValueError("Invalid axis caption")
                            if value:
                                captions.append(f"{label}：{value}")
                    if captions:
                        description = props.get("description", "")
                        if not isinstance(description, str):
                            raise ValueError("Invalid description")
                        props["description"] = " · ".join(filter(None, [description, *captions]))
                    if node["type"] == "metric":
                        columns, rows = props.get("columns"), visual.get("rows")
                        if rows and not visual.get("items"):
                            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(columns, list) or not columns or len(columns) > 12:
                                raise ValueError("Ambiguous metric rows")
                            row = rows[0]
                            if not isinstance(row, dict) or set(row) - {"id", "label", "values"} or not isinstance(row.get("values"), dict):
                                raise ValueError("Invalid metric row")
                            if set(row["values"]) != {c.get("key") for c in columns if isinstance(c, dict)}:
                                raise ValueError("Metric columns do not match values")
                            items = []
                            for column in columns:
                                if not isinstance(column, dict) or set(column) - {"key", "label", "unit"} or not isinstance(column.get("key"), str) or not isinstance(column.get("label"), str):
                                    raise ValueError("Invalid metric column")
                                items.append({"id": column["key"], "label": column["label"], "value": row["values"][column["key"]],
                                              "description": row.get("label", ""), **({"unit": column["unit"]} if "unit" in column else {})})
                            props.pop("columns")
                            visual.pop("rows")
                            visual["items"] = items
                        for item in visual.get("items", []):
                            if isinstance(item, dict) and "unit" in item:
                                unit = item.pop("unit")
                                if not isinstance(unit, str) or len(unit) > 40 or not isinstance(item.get("label"), str):
                                    raise ValueError("Invalid metric unit")
                                if unit and props.get("unit") and unit != props["unit"]:
                                    raise ValueError("Conflicting metric units")
                                if unit and not props.get("unit"):
                                    item["label"] += f"（{unit}）"
                node["visual"] = visual
        if node.get("type") in RAILS and isinstance(node.get("props"), dict) and not set(node["props"]) - {"title", "description"}:
            for source, target in (("title", "label"), ("description", "text")):
                if source in node["props"]:
                    value = node["props"][source]
                    if not isinstance(value, str) or (node.get(target) and node[target] != value):
                        raise ValueError("Conflicting rail metadata")
                    node[target] = value
            node.pop("props")
        children = node.pop("items", None) if node.get("type") in RAILS else None
        blocks.append(node)
        if children is not None:
            if not isinstance(children, list) or len(children) > 12:
                raise ValueError("Invalid rail items")
            for child in children:
                if not isinstance(child, dict) or child.get("type", "rail_item") != "rail_item" or child.get("parent_id", node.get("id")) != node.get("id"):
                    raise ValueError("Conflicting rail child")
                for source, target in (("title", "label"), ("description", "text")):
                    if source in child:
                        value = child.pop(source)
                        if not isinstance(value, str) or (child.get(target) and child[target] != value):
                            raise ValueError("Conflicting rail item fields")
                        child[target] = value
                blocks.append({**child, "type": "rail_item", "parent_id": node.get("id")})
    if len(blocks) > 160:
        raise ValueError("Too many document nodes")
    upsert["blocks"] = blocks
    return result
