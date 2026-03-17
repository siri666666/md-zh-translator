# md-zh-translator

把英文 `Markdown` 文章翻译成中文，支持自定义 OpenAI 兼容 API，并尽量保留原始格式。

## 功能

- 翻译英文 Markdown 为简体中文
- 自定义接口参数：`base_url`、`api_key`、`model`
- 保留 Markdown 结构（标题、列表、表格、链接等）
- 默认自动清理版权/许可声明等噪声块（可关闭）
- 默认自动修复 OCR 导致的段内断行（可关闭）
- 默认跳过不应翻译内容：
  - 代码块、行内代码
  - URL、链接定义
  - 公式块与行内公式
  - `References/Bibliography` 等参考文献区域
  - 典型引用行（如 `[1] ...`, `doi: ...`）

## 安装

```bash
cd md-zh-translator
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

或直接：

```bash
pip install -r requirements.txt
pip install -e .
```

## 配置

复制并填写环境变量：

```bash
copy .env.example .env
```

`.env` 示例：

```env
MDT_API_KEY=your_api_key_here
MDT_BASE_URL=https://api.openai.com/v1
MDT_MODEL=gpt-4o-mini
MDT_MAX_RPM=60
MDT_MAX_TPM=120000
```

## 使用

```bash
md-zh-translator -i examples/sample_en.md -o examples/sample_zh.md --verbose
```

常用参数：

- `--api-key`：覆盖环境变量 API Key
- `--base-url`：覆盖环境变量 Base URL
- `--model`：覆盖环境变量模型名
- `--max-chars`：每次请求的分片字符数（默认 `2600`）
- `--max-rpm`：每分钟最大请求数（RPM）
- `--max-tpm`：每分钟最大令牌数（TPM，先本地估算后按响应 usage 校正）
- `--translate-references`：默认不翻译参考文献，打开此参数后会尝试翻译
- `--dry-run`：不调用 API，只跑保护/还原流程；默认逐字符透传输入
- `--keep-boilerplate`：关闭自动清理版权/许可噪声块
- `--keep-ocr-linebreaks`：关闭自动修复 OCR 段内断行
- `--strict-integrity`：开启严格完整性校验（公式/链接/图片/代码围栏/占位符），异常即返回非 0

## 示例

```bash
md-zh-translator -i examples/sample_en.md -o examples/sample_zh.md
```

默认输出文件名规则：

- 输入 `paper.md` -> 输出 `paper.zh.md`

## 注意事项

- 建议输入文件使用 UTF-8 编码。
- 模型偶尔会改写占位符，程序内置占位符守护重试，仍失败时会回退该分片原文，避免公式/代码丢失。
- `--strict-integrity` 推荐用于正式文档批处理，可在结构异常时直接让流程失败，便于 CI 或脚本拦截。
- 如果供应商有 RPM/TPM 限制，建议显式配置 `--max-rpm` 和 `--max-tpm` 以减少 429。
