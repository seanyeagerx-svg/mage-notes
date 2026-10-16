# 魔法师手札 · Mage Notes

Windows 本地语音草稿工具。把语音变成可保存的文字，积累自己的词库，并整理创作需求。

**公开测试版 `1.0.0-beta10-public.2`** · Python 源码交付 · MIT（本项目代码）

[安装](#安装与启动) · [真实演示](DEMO.md) · [运行验证](VALIDATION.md) · [第三方说明](THIRD_PARTY_NOTICES.md)

![合成音频实测记录，非软件界面截图](examples/recognition-preview.png)

## 能做什么

- 本地中文流式识别；可在窗口中试写，也可配置快捷键向其他应用输入。
- 草稿自动保存、恢复和本地历史；个人词库与校对记录独立保存在本机。
- 整理图片、配色和参考资料需求；这些入口负责整理与交接，实际生成依赖目标服务。
- 可选 GLM / Codex 辅助校对，需要自行配置或登录并主动启用。

公开包不含私人视频 SOP、原始工作流模板、个人词库、任何第三方 Skill、密钥、录音或模型权重。
视频制作与声源分离入口不在本次交付范围。基础中文识别可独立使用。

## 安装与启动

已实测 Windows、Python **3.14.6**，CPU 识别。请安装带 Tcl/Tk 的标准版 Python 3.14，
从 [python.org](https://www.python.org/downloads/windows/) 获取；模型约 160 MiB，另需依赖安装空间。

1. 从 [版本源码包](https://github.com/seanyeagerx-svg/mage-notes/archive/refs/tags/v1.0.0-beta10-public.2.zip)
   下载 ZIP，**完整解压**到可写目录。
2. 双击 `Install.cmd`，只会在当前目录建立 `.venv` 并安装依赖。
3. 双击 `DownloadModels.cmd`，下载固定版本的基础中文模型并校验散列。
   下载会联网；模型使用与许可请阅读[上游说明](THIRD_PARTY_NOTICES.md)。
4. 双击 `Launch.cmd`，等待识别模型就绪，先在窗口里试写。

等价的 PowerShell 操作，在解压目录执行：

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe download_model.py
.\.venv\Scripts\python.exe app.py
```

模型也可由自己从上游准备。`models/streaming-zh/` 必须有 `encoder.int8.onnx`、
`decoder.onnx`、`joiner.int8.onnx`、`tokens.txt` 四个文件。仓库没有附带它们。
增强模型缺失时回退基础识别，设置中也可选择“原始”模式。

## 先验证，再接入日常输入

```powershell
.\.venv\Scripts\python.exe scripts/transcribe_file.py examples/dictation-demo.wav
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe app.py --no-hooks
```

第一条实际识别仓库中的合成中文音频，不打开麦克风、不调用云服务。
`--no-hooks` 关闭全局快捷键，适合先查看窗口；**点击试写会开始录音**。
正常启动时默认 Alt 触发听写、鼠标侧键 X2 触发工作流，可在设置里更换。
向外部窗口输入可能受目标应用权限影响，建议先在空白文本中测试。

## 数据与联网

本地草稿、历史与个人学习数据写入 `data/`，偏好写入 `settings.json`；这些目录和文件已被忽略。
默认 `ai_assist=false`、`audio_learning=false`。启用云辅助后可能发送当前文字及短上下文，
GLM 可能按服务商规则计费；本次发布未测试付费服务、Codex 账号调用或向第三方窗口发送。
API Key 使用 Windows DPAPI 存入本机 `data/`，不随公开包提供。

安装与模型下载使用系统现有网络。脚本不会修改 VPN、系统代理、DNS、防火墙、路由或开机启动。
没有独立更新器，也没有打包 EXE；无需管理员权限安装本项目本身。

## 已知边界与排错

- 识别会有错字。演示中“本机”识别为“本期”，原始结果保留，需人工复核。
- 提示模型缺失：检查四个文件是否完整，或重新运行 `DownloadModels.cmd`。
- 提示音频设备问题：在设置里重新选择设备；本轮未做真人麦克风验收。
- `public.2` 修复外部输入框切换时的误输：发送文字及回改前重新核对原窗口、原输入框和所属进程。
- 提示可选增强模型不存在：基础听写仍可用，不必下载全部增强模型。
- 运行失败：在 PowerShell 运行上面的命令查看提示，公开反馈前删除私人路径和内容。
- 外部输入已在独立进程的原生文本框实测；未验证所有编辑器、第二台电脑、长期运行、全部历史功能或所有可选模型，详见 [VALIDATION.md](VALIDATION.md)。
