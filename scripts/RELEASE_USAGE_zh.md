# md-zh-translator 免安装版（Windows / Linux）

## 快速使用

### Windows

1. 打开 PowerShell，进入当前目录。
2. 运行：

```powershell
.\md-zh-translator.exe --help
```

3. 执行翻译：

```powershell
.\md-zh-translator.exe `
  -i "C:\path\input.md" `
  -o "C:\path\input.zh.md" `
  --api-key "<YOUR_KEY>" `
  --base-url "https://api.deepseek.com/v1" `
  --model "deepseek-chat" `
  --max-rpm 30 `
  --max-tpm 60000 `
  --strict-integrity `
  --verbose
```

### Linux

1. 打开终端进入当前目录。
2. 赋予执行权限并查看帮助：

```bash
chmod +x ./md-zh-translator
./md-zh-translator --help
```

3. 执行翻译：

```bash
./md-zh-translator \
  -i "/path/input.md" \
  -o "/path/input.zh.md" \
  --api-key "<YOUR_KEY>" \
  --base-url "https://api.deepseek.com/v1" \
  --model "deepseek-chat" \
  --max-rpm 30 \
  --max-tpm 60000 \
  --strict-integrity \
  --verbose
```

## 说明

- 不需要安装 Python 或执行 `pip install`。
- 保留 Markdown 结构，默认跳过参考文献等不应翻译内容。
- 建议始终带 `--strict-integrity`，异常时会返回非 0 退出码。
