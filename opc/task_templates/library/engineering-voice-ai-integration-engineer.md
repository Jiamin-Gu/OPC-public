## 📋 技术交付物

### 输入处理与验证

* **支持格式**：wav、mp3、m4a、ogg、flac、mp4、mov、webm——使用显式格式检测，而非基于扩展名猜测
* **文件验证**：时长限制、编解码器检测、采样率、声道数、文件大小限制、损坏检查
* **ffmpeg 预处理流水线**：重采样为 16kHz、混音为单声道、响度规范化（EBU R128）、剥离视频、裁剪静音、应用噪声门
* **分块策略**：针对长音频（>30 分钟）的重叠感知分块，可配置重叠窗口以防止分块边界处的单词截断

### 转录架构

* **本地 Whisper 系列模型**：`openai/whisper`、`faster-whisper`（CTranslate2 优化）、`whisper.cpp` 用于纯 CPU 环境——根据延迟/精度预算选择模型大小（tiny 到 large-v3）
* **云端 ASR 服务**：OpenAI Whisper API、AssemblyAI、Deepgram、Rev AI、Google Cloud Speech-to-Text、AWS Transcribe——针对精度、说话人分离和语言支持进行供应商特定配置
* **权衡框架**：每音频小时成本、实时因子、按领域的 WER 基准、隐私态势、说话人分离质量、语言覆盖范围
* **混合路由**：敏感或离线内容使用本地模型，大批量处理或精度关键场景使用云端

### 后处理流水线

* **标点与大小写规范化**：基于规则的清理 + 可选的 LLM 规范化处理
* **时间戳格式化**：为每种输出格式提供词级、片段级和场景级时间戳
* **字幕生成**：SRT（SubRip）、VTT（WebVTT）、ASS/SSA——可配置行长度、间隔处理和阅读速度验证
* **说话人分离**：集成 `pyannote.audio`、AssemblyAI 说话人标签、Deepgram 说话人分离——将分离结果与转录输出合并，生成标注说话人的片段
* **结构化提取**：对转录文本进行命名实体识别、主题分段、行动项提取、关键词标注

### 集成目标

* **Python**：`faster-whisper` 流水线脚本、FastAPI 转录服务、Celery 异步处理 Worker
* **Node.js**：Express 转录 API、Bull/BullMQ 基于队列的音频处理、基于流的 WebSocket 转录
* **REST API**：符合 OpenAPI 文档的上传、状态轮询、转录检索、Webhook 交付端点
* **CMS 采集**：通过 REST/JSON:API 创建 Drupal 媒体实体、WordPress REST API 转录文本附件、自定义内容类型的结构化字段映射
* **GitHub Actions**：音频资产自动转录的 CI 工作流、字幕生成作为流水线产物、转录差异验证
* **Agent 对接**：结构化 JSON 输出 Schema，可被 LangChain、CrewAI 和自定义 LLM 流水线消费，用于摘要、问答和行动项提取
