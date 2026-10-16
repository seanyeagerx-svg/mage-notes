# 第三方组件与模型

MIT 许可覆盖本项目自行编写的应用代码，不把第三方依赖、模型、系统字体或语音引擎变成 MIT。
依赖由 pip 从各自上游安装，仓库没有复制供应商源码、可执行程序、字体、模型或第三方 Skill。

| 用途 | 上游 |
|---|---|
| 语音识别 | [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) |
| 数值、音频 | [NumPy](https://numpy.org/)、[sounddevice](https://python-sounddevice.readthedocs.io/)、[soxr](https://github.com/dofuuz/python-soxr) |
| 中文处理 | [pypinyin](https://github.com/mozillazg/python-pinyin)、[jieba](https://github.com/fxsjy/jieba)、[OpenCC](https://github.com/BYVoid/OpenCC) |
| Windows COM | [comtypes](https://github.com/enthought/comtypes) |
| Python / 窗口 | [Python](https://www.python.org/)、[Tcl/Tk](https://www.tcl.tk/) |

基础模型下载器引用
[`csukuangfj/sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30`](https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30)
固定 revision `ad658fa0201659a09ea3c176129a191c77ecae8f`，只下载并校验所需模型与上游 README。
模型不随源码重新分发。模型条款应以相应上游说明为准，不能从 sherpa-onnx 运行库的许可推断模型许可。
可选双语或增强模型同理，未包含在本次演示和验证中。

演示使用本项目新写的文案、自制图形及 Windows Microsoft Huihui Desktop 合成语音。
它不是用户录音；仓库没有分发语音引擎、系统字体或任何真人音色数据。
