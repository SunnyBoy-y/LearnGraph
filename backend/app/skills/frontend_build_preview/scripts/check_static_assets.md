# `check_static_assets.py` — 校验产物自包含

> 扫描 `dist/` 的 HTML/CSS/JS 等文本文件，找出 `http(s)://` 与协议相对外链引用。预览/发布要求**零外链**（离线 CSP 约束）。

## 用法

```bash
python scripts/check_static_assets.py --dir my-app
python scripts/check_static_assets.py --dir my-app --max-external 2
```

## 输入 / 输出

- 输入：`<dir>/dist/`。
- 输出：stdout JSON（`external_files` 按文件列外链、`external_total`、`within_limit`）。

## 参数

| 参数 | 说明 |
|---|---|
| `--max-external` | 允许的外链总数（默认 0；超过则退出码非零） |

## 最佳组合

```text
build_frontend ──check_static_assets──> 通过 → render_preview / 发布
check_static_assets 报外链 ──> 内联资源后重构建
```

## 限制与失败

- 只扫文本扩展名（html/css/js/mjs/json/svg/txt/md）。
- 外链超过 `--max-external` → 失败；把资源内联（如 `vite-plugin-singlefile`）后再构建。
