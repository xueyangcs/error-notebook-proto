# K-12 作业照片批改原型

本地 Python CLI：输入一张含题干与学生作答的图片，调用 Gemini 3.6 Flash 多模态接口，输出结构化批改 JSON，并本地渲染为可读 Markdown 报告；另附最小 FastAPI 网页。

## 1. 申请免费 API Key

1. 打开 [Google AI Studio](https://aistudio.google.com/apikey)
2. 使用 Google 账号登录，创建一个 API key
3. **不要把密钥写入仓库或 `.env` 提交进 git**，只放到当前 shell 环境变量

```bash
export GEMINI_API_KEY='你的密钥'
```

## 2. 安装

需要 Python 3.11+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. 运行

```bash
python grade_image.py samples/math_2x_plus_1.png
python grade_image.py samples/math_2x_plus_1.png --format md
python grade_image.py path/to/image.jpg --level 初中 --subject 数学 --format json
python grade_image.py path/to/image.jpg --model gemini-3.5-flash-lite
```

成功时默认写入 `out/<文件名>.md`（`--format json` 时额外写入 `.json`），并打印 correctness / confidence 摘要。模型先返回 JSON，再由本地 `report.py` 转成 Markdown 报告。若模型返回无法解析的内容，会把原文落到 `out/<文件名>.raw.txt` 并以非 0 退出。主模型失败时依次尝试 `gemini-2.5-flash` → `gemini-flash-latest` → `gemini-2.0-flash`。

缺少 `GEMINI_API_KEY` 时退出码为 `2`，并打印申请说明。

### 可选参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--level` | `初中` | `小学` / `初中` / `高中` |
| `--subject` | `数学` | 学科，写入 prompt |
| `--model` | `gemini-3.5-flash-lite` | AI Studio 当前可用 Flash；`gemini-2.5-flash` 对新账号已下线，备选 `gemini-flash-latest` / `gemini-2.0-flash` |
| `--format` | `md` | `md` / `json`；始终写 `.md`，`json` 时另写 `.json` |
| `--out-dir` | `out` | 输出目录 |

## 4. 输出字段

```json
{
  "problem_text": "题干转录",
  "student_solution": "学生作答转录",
  "correctness": "correct|partial|incorrect|unknown",
  "score_hint": 100,
  "error_spans": [],
  "step_critique": [{"step": 1, "comment": "点评", "ok": true}],
  "correct_solution_sketch": "正确解法提纲",
  "confidence": 0.9,
  "notes": "补充说明"
}
```

`samples/math_2x_plus_1.png` 为合成样例（印刷题干「求 2x+1=7」+ 仿手写作答「解: x=3」）。

## 5. 测试

不调用 API，只测 JSON 解析与字段校验：

```bash
python -m unittest discover -s tests -v
```


## 6. 网页（FastAPI）

```bash
export GEMINI_API_KEY='你的密钥'
uvicorn web_app:app --host 0.0.0.0 --port 8080
# 或：python web_app.py
# Docker：docker build -t error-notebook . && docker run -e GEMINI_API_KEY -p 8080:8080 error-notebook
```

浏览器打开 `http://127.0.0.1:8080/`，上传图片并选择学段/学科。

## 说明

- 密钥只读取环境变量 `GEMINI_API_KEY`
- 每次运行对一张图做 **一次** 多模态调用
- 本原型不做多题切分、错题本持久化或 Mathpix OCR
