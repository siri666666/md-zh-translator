# md-zh-translator

把英文 `Markdown` 文章翻译成中文，支持自定义 OpenAI 兼容 API，并尽量保留原始格式。

## 功能

- 翻译英文 Markdown 为简体中文
- 自定义接口参数：`base_url`、`api_key`、`model`
- 保留 Markdown 结构（标题、列表、表格、链接、公式、代码围栏）
- 默认自动清理版权/许可声明等噪声块（可关闭）
- 默认自动修复 OCR 导致的段内断行（可关闭）
- 默认跳过参考文献与典型引用行
- 遇到 `429` 立即终止并提示降低并发

## 运行方式（无需构建）

```bash
cd md-zh-translator
pip install -r requirements.txt
python md_zh_translator.py --help
```

不需要配置环境变量，直接命令参数传入即可。

## 使用

```bash
python md_zh_translator.py \
  -i examples/sample_en.md \
  -o examples/sample_zh.md \
  --api-key "<YOUR_KEY>" \
  --base-url "https://api.deepseek.com/v1" \
  --model "deepseek-chat" \
  --concurrency 4 \
  --verbose
```

常用参数：

- `--api-key`：API Key（真翻译时必填）
- `--base-url`：API Base URL（默认 `https://api.openai.com/v1`）
- `--model`：模型名（默认 `gpt-4o-mini`）
- `--concurrency`：固定并发线程数（必填）
- `--max-chars`：每次请求的分片字符数（默认 `2600`）
- `--max-retries`：请求失败重试次数（不含 429）
- `--timeout`：请求超时秒数
- `--translate-references`：默认不翻译参考文献，打开后会尝试翻译
- `--dry-run`：不调用 API，只跑保护/还原流程
- `--keep-boilerplate`：关闭自动清理版权/许可噪声块
- `--keep-ocr-linebreaks`：关闭自动修复 OCR 段内断行
- `--strict-integrity`：开启严格完整性校验（结构 + 占位符 + 疑似漏翻）
- `--verbose`：输出详细日志（并发信息、分片进度、补救统计）；不影响最终翻译内容

默认输出文件名规则：

- 输入 `paper.md` -> 输出 `paper.zh.md`

## 检查更新（一键）

更新检查与更新执行由独立脚本触发，不依赖本地 Git 仓库。

- Windows：双击 `check_update.bat`
- Linux/macOS：`bash check_update.sh`
- 或直接：`python check_update.py`

更新流程：

1. 拉取 `https://github.com/siri666666/md-zh-translator/releases/latest/download/update.json`
2. 显示当前版本、最新版本、发布时间、更新内容与详情链接
3. 询问 `y/n`，确认后下载发布包并校验 `sha256`
4. 自动备份后覆盖更新；失败自动回滚

默认只保留最近 3 份备份，备份目录为 `.mdzt_update_backups/`。

## 自动化发版（Tag 触发）

仓库内置自动发版工作流：推送 `v*` tag 后自动创建/复用 Release，并上传：

- `md-zh-translator.zip`
- `update.json`

推荐发版步骤：

```bash
# 1) 同步版本号（至少这两个文件）
# pyproject.toml -> [project].version
# src/md_translate_zh/__init__.py -> __version__

# 2) 提交代码
git add .
git commit -m "release: v0.1.1"

# 3) 打 tag 并推送
git tag v0.1.1
git push origin main --tags
```

注意：

- 用户下载时请使用 Release 中的 `md-zh-translator.zip`，不要使用 GitHub 自动生成的 `Source code (zip/tar.gz)`。
- `check_update.py` 依赖 `releases/latest/download/update.json`，所以每次发版都必须包含 `update.json` 资产。

## 注意事项

- 建议输入文件使用 UTF-8 编码。
- 模型偶尔会改写占位符，程序会先做常规重试，再做子分片补救；补救失败会记录为“补救失败分片”。
- `--strict-integrity` 推荐用于正式文档批处理：出现结构异常、未还原占位符、补救失败分片、疑似漏翻分片时直接失败。
- 当服务商返回 `429` 时，程序会立即失败并提示降低 `--concurrency` 后重试。
