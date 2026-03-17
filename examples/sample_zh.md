# 轻量级翻译管道入门

这篇短文演示了如何在翻译自然语言时保持 Markdown 结构。

## 要点

- 保持列表结构不变。
- 不要翻译内联代码，例如 `pip install requests`。
- 保持链接有效：[项目页面](https://example.com/docs?id=42)。

```python
def add(a, b):
    return a + b
```

## References

[1] Vaswani, A., et al. (2017). Attention Is All You Need.
[2] Brown, T., et al. (2020). Language Models are Few-Shot Learners.
doi: 10.48550/arXiv.2005.14165
