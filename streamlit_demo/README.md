# Memo Alive · 千问真实交互站

这是现有 React/FastAPI 项目的独立 Streamlit 副本。它不会导入原后端、连接原 PostgreSQL，也不会读写根目录的 `data/audio`。

## 已包含

- 浏览器录音与音频上传兜底
- 千问真实语音识别、内容整理、关系判断与向量检索
- 原始音频、原始转写只读保存
- 所有千问结构化结果经 Pydantic 校验后才写入 SQLite
- 历史记录、整理稿版本、人工审批与主题时间线

本副本不包含问答、TTS、登录和多人长期存储。它只有千问真实模式，不会在千问失败时切换供应商、固定文字、规则关系或本地伪向量。

## 固定模型

- 语音识别：`qwen3-asr-flash`
- 备忘录整理与关系判断：`qwen3.7-plus-2026-05-26`
- 向量：`qwen3.7-text-embedding`，固定 1024 维

## 本地运行

推荐使用 Python 3.11 或 3.12。先确认 `python3 --version`，再创建独立环境：

```bash
cd "<PROJECT_ROOT>"
python3 -m venv .venv-streamlit
.venv-streamlit/bin/pip install -r streamlit_demo/requirements.txt
export DASHSCOPE_API_KEY="你的百炼 API Key"
.venv-streamlit/bin/streamlit run streamlit_demo/app.py
```

应用未配置 `DASHSCOPE_API_KEY` 时会明确停止，不会生成模拟结果。Key 不得写入代码、README 或 Git。

默认百炼端点为：

```text
https://dashscope.aliyuncs.com/compatible-mode/v1
```

如果你的 Key 属于特定业务空间或地域，请明确设置与该 Key 匹配的端点，不要自动跨地域切换：

```bash
export DASHSCOPE_COMPATIBLE_BASE_URL="你的百炼兼容端点"
```

## 音频与隐私边界

- 支持 WAV、MP3、WebM、OGG。
- 原始音频先写入当前会话的独立目录，再发送给阿里云百炼。
- `qwen3-asr-flash` 使用 Base64 Data URI；编码后的完整请求音频不得超过 10 MB。
- 任何千问阶段失败都会保留已经保存的音频和原始转写，但不会写入不完整的 memo 或关系。
- 公网链接会消耗百炼额度并接受访客音频，只适合邀请制 Demo；不要上传敏感录音。

## 存储模式

默认 `DEMO_STORAGE_MODE=session`：

- 每个浏览器会话使用独立的临时 SQLite 和音频目录；
- 不同访客不会看到彼此的数据；
- 页面刷新、云端冷启动或重新部署后，数据可能消失。

本地单用户若需要重启后保留数据，可使用：

```bash
export DEMO_STORAGE_MODE=persistent
export DEMO_DATA_DIR="/一个明确的本地目录"
```

不要把 `DEMO_DATA_DIR` 指向现有项目的 `data/audio`，也不要复用之前由其他向量模型建立的数据库。

## 部署到 Streamlit Community Cloud

1. 将仓库推送到 GitHub。
2. 在 Streamlit Community Cloud 创建应用。
3. Entrypoint 填写 `streamlit_demo/app.py`。
4. 在部署后台的 Secrets 中配置：

```toml
DASHSCOPE_API_KEY = "你的百炼 API Key"
DASHSCOPE_COMPATIBLE_BASE_URL = "与你的 Key 匹配的端点"
DEMO_STORAGE_MODE = "session"
```

5. 部署后会得到一个 `https://...streamlit.app` 地址。

## 验证

```bash
PYTHONPATH=. .venv-streamlit/bin/pytest -q streamlit_demo/tests
.venv-streamlit/bin/python -m compileall -q streamlit_demo
DASHSCOPE_API_KEY=test-only .venv-streamlit/bin/streamlit run streamlit_demo/app.py --server.headless true --server.port 8501
curl -fsS http://localhost:8501/_stcore/health
```

自动测试中的 `test-only` Key 只用于验证页面启动和请求契约，不会发起真实模型调用。真实模型烟测需要单独配置有效且低额度的百炼 Key。
