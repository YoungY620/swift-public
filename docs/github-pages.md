# GitHub Pages 公共看板

`site/` 是纯静态页面，不需要 Node、构建工具或后端。它只读取经过校验的公开简报，不应访问私有报告目录。

## 发布数据

```bash
python3 scripts/publish_public_brief.py \
  path/to/public-brief.json \
  --site-data site/data
python3 scripts/validate_public_brief.py site/data
```

发布脚本会：

1. 严格验证字段、价格差计算、Polymarket event 链接形态和隐私边界。
2. 写入以 `brief_id` 命名的不可变 JSON；同一 ID 内容不同会被拒绝覆盖。
3. 更新 `latest.json`。
4. 按 `generated_at` 倒序维护 `index.json`。

如果在定时任务环境中设置 `FORECAST_EDGE_SITE_DATA_DIR`，任务完成后会自动执行相同步骤，但不会自动提交或推送 Git。

## 启用 GitHub Pages

仓库包含 `.github/workflows/pages.yml`。为保持 Pages 完全可选，workflow 只有在仓库变量 `ENABLE_FORECAST_EDGE_PAGES` 等于 `true` 时才部署。启用时：

1. 在目标 GitHub 仓库的 Pages 设置中选择 **GitHub Actions** 作为来源。
2. 添加仓库变量 `ENABLE_FORECAST_EDGE_PAGES=true`。
3. 将验证后的 `site/data/` 提交并推送到 `main`，或手动运行 workflow。

GitHub Pages 面向公开内容。若目标仓库或组织的免费套餐不支持当前可见性下的 Pages，请使用公开仓库，或在其他静态托管环境直接发布同一 `site/` 目录。

## 本地预览

浏览器的 `fetch` 通常不能直接读取 `file://`，请启动本地静态服务器：

```bash
python3 -m http.server 8080 --directory site
```

打开 `http://localhost:8080/`。页面支持移动端、键盘焦点和减少动态效果偏好；证据默认折叠。
