# An Intro to Lightweight Translation Pipelines

This short article demonstrates how to preserve Markdown structure while translating natural language.

## Key Points

- Keep list structure unchanged.
- Do not translate inline code like `pip install requests`.
- Keep links valid: [Project Page](https://example.com/docs?id=42).

```python
def add(a, b):
    return a + b
```

## References

[1] Vaswani, A., et al. (2017). Attention Is All You Need.
[2] Brown, T., et al. (2020). Language Models are Few-Shot Learners.
doi: 10.48550/arXiv.2005.14165
