# 本地识别与草稿演示

这是合成音频文件的真实识别结果与窗口草稿保存验证，**不是麦克风录音或软件界面录像**。

- [播放或下载演示视频](examples/demo.mp4)
- [输入音频](examples/dictation-demo.wav) · [合成文案](examples/dictation-script.txt)
- [未经人工纠错的实际识别输出](examples/transcript.json)

![实际识别记录](examples/recognition-preview.png)

复现：完成安装和基础模型准备后，在仓库目录运行：

```powershell
.\.venv\Scripts\python.exe scripts/transcribe_file.py examples/dictation-demo.wav
```

文案最后是“本机处理”，实际输出是“本期处理”。模型与系统不同，结果和耗时可能变化。
本机验证还实际启动了应用窗口，在不注册全局快捷键、不打开麦克风的模式下，
把识别原文放入草稿，保存后重新读取并逐字比较，随后正常退出。
以上演示只证明文件识别与保存链路，不能替代真人收音或长期稳定性验收。

`public.2` 另完成了文件音频 → 实际识别模型 → App → 独立进程原生输入框的测试，
以及切换窗口/输入框和发送前焦点变化等 9 项实测。
[查看实测记录](validation/external-input-2026-10-16.json)；这些记录全部使用合成输入，不含用户录音。
