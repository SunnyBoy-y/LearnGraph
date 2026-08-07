# `json_transform.py` — JSON 变换/过滤

> 对一个 JSON 文档做字段选择、行过滤、数量限制，输出新 JSON。适用于对接口结果/数据文件做提取与清洗。

## 用法

```bash
python scripts/json_transform.py --input inputs/data.json --output outputs/out.json --select name,score
python scripts/json_transform.py --input data.json --output out.json --filter "score=ge=60" "name=contains=张"
python scripts/json_transform.py --input data.json --output out.json --limit 20
```

## 参数

| 参数 | 说明 |
|---|---|
| `--select` | 保留的字段（支持 `a.b` 点路径），多个空格分隔 |
| `--filter` | 过滤 `key=op=value`，支持 `eq/ne/gt/lt/ge/le/contains/in` |
| `--limit` | 最多保留条目数 |

## 输入 / 输出

- 输入：`.json`（对象或数组）。
- 输出：`.json`。stdout 打印条目数/bytes/sha256。

## 最佳组合

```text
json_transform ──> 干净.json ──make_report──> 报告.md
json_transform ──> 图表数据 ──> frontend 渲染
```

## 限制与失败

- `--select`/`--filter` 需要对象条目；纯数组值会报错。
- 点路径找不到字段 → 该字段为 `null`（不报错）。
