# 教材配套 labs

每章一个可运行示例 `ch_x_y.py` 与一个验收测试 `test_ch_x_y.py`。**全部离线可跑，无需 API Key**——默认使用 `TestModel` / `FunctionModel`。

## 运行方式

本目录与 pydantic-ai 仓库主 workspace 隔离，请用 `uv run --no-project`：

```bash
cd textbook/labs

# 单章验收测试
uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest part_1/test_ch_1_1.py -q

# 全部验收测试
uv run --no-project --with "pydantic-ai-slim,pytest" python -m pytest -q

# 直接运行示例
uv run --no-project --with "pydantic-ai-slim" python part_1/ch_1_1.py
```

篇零的 labs 只需要标准库与 pytest：

```bash
uv run --no-project --with pytest python -m pytest part_0 -q
```

## 约定

- `ch_x_y.py`：示例模块，`python ch_x_y.py` 可直接运行并打印结果。
- `test_ch_x_y.py`：验收测试，断言示例的关键行为；全绿 = 该章已掌握。
- 需要真实模型时，设置对应环境变量（如 `OPENAI_API_KEY`），并把 `TestModel()` 换成真实模型字符串。
- 篇五起会用到额外 extra（如 `pydantic-ai-slim[temporal]`、`pydantic-ai-slim[ui]`），对应章节会单独说明运行命令。
