# 文档对照

登录后上传两份 Word 文档，使用参照文档的排版生成一份新的目标文档。可下载本次结果，也可以在个人记录里重新下载最近 50 次生成的文件。

## 启动

需要启动 Docker Desktop，并启用 Docker Compose。在 PowerShell 中运行：

```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

打开 [本地工作台](http://127.0.0.1:8088)，创建账号后选择一份参照文档和一份目标文档。每份文件支持标准 `.docx`，最大 10 MB。旧版 `.doc` 或 PDF 可以先在 Word 中另存为 `.docx`。查看服务状态用 `docker compose ps`，查看日志用 `docker compose logs -f`，停止服务用 `docker compose down`。MySQL 和 Redis 的数据保存在各自的数据卷里。

## 接入阿里云百炼

1. 在本地 `.env` 设置 `DASHSCOPE_API_KEY`。也可以在运行 Compose 的 PowerShell 中预先设置同名环境变量。密钥只注入 Python 服务。
2. 将 `DASHSCOPE_BASE_URL` 设置为百炼控制台中对应工作空间与地域的 OpenAI 兼容地址，结尾为 `/compatible-mode/v1`。示例使用中国北京的共享地址。
3. 默认用 `qwen-plus` 分类、`text-embedding-v4` 检索，可按账号实际开通的模型改用其他兼容模型。修改配置后执行 `docker compose up -d`。
4. 提交任务前打开 **百炼辅助识别**。已明确标记的 Word 样式优先。遇到待判断的短段落时，Python 用向量相似度从自有知识库取三条规则，再让模型判断段落角色。向量调用失败时改用本地词项检索。模型调用失败时用本地规则继续处理，任务结果会标明模式。

开启辅助识别时，每份文档最多有 32 个待判断的短段落发送给百炼，每段最多 90 个字符。RAG 检索会将部分短段落及知识库规则送去生成向量。关闭后整份文档留在本地服务内。

可直接编辑 [agent/knowledge.json](agent/knowledge.json) 添加或调整知识库条目。每条包含唯一的 `id`、`title` 和 `rule`。Compose 将文件挂载为只读，Python 在后续任务检测到修改时重新建立向量索引。当前知识库规模使用进程内向量检索即可。

[百炼兼容接口与地域地址](https://docs.modelstudio.console.alibabacloud.com/en/model-studio/base-url) · [百炼文本向量 API](https://docs.modelstudio.console.alibabacloud.com/zh/model-studio/text-embedding-synchronous-api)

## 套版范围

- 按总标题、标题层级、正文、列表和表格段落匹配参照文档中的样式。可迁移字体、字号、颜色、段落对齐、间距、缩进等属性。参照文档缺少对应角色时，保留该角色原格式。列表保留自身编号和缩进。
- 迁移参照文档已有的页面尺寸、页边距，以及表格边框、底纹、单元格内边距等选定样式。保留目标文档的节结构、表格行列与合并关系。
- Python 只更新目标包的正文 XML 中列入白名单的格式属性，并在输出前核对其余正文结构。图片、页眉页脚、注释、链接与媒体部件继续使用目标文档中的数据。不同长度的文档可能产生不同分页。复杂域、文本框、页眉页脚排版与绘图样式保留原样。

## 服务结构

| 服务 | 职责 |
| --- | --- |
| React 19.3 / TypeScript 7 / Vite 8 | 登录、选择文件、启动套版、任务记录和下载 |
| Java 21 / Spring Boot | 密码哈希、会话鉴权、上传入口、任务与下载授权 |
| Python / FastAPI | DOCX 套版、内容校验、百炼调用、RAG 检索 |
| MySQL | 账号、任务和生成的 DOCX |
| Redis | 登录会话和 CSRF 状态 |

前端通过同源 `/api` 访问 Java。Python、MySQL、Redis 只在 Compose 内网监听。默认配置用于本机开发，Compose 入口绑定到回环地址。

## 开发验证

```powershell
# 在项目根目录执行
python -m pip install -r agent/requirements.txt
python -m unittest discover -s agent -p "test_*.py"
mvn -f backend/pom.xml package
Push-Location frontend
npm ci
npm run build
Pop-Location
```

前端开发需要 Node.js 22.12+ 或 24+。单独启动 Java 时可用 `JDBC_URL`、`MYSQL_USER`、`MYSQL_PASSWORD`、`REDIS_HOST`、`REDIS_PORT`、`REDIS_PASSWORD`、`AGENT_URL` 和 `AGENT_TOKEN` 连接本地服务。单独启动 Python 时，从 `agent` 目录运行 `python -m uvicorn server:app` 并设置相同的 `AGENT_TOKEN`。从 `frontend` 目录运行 `npm run dev` 时，Vite 会将 API 请求转发到 Java 的 8080 端口。
