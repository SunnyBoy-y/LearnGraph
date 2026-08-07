# 常见失败与处理（media-processing）

## 不支持的编码/损坏文件

- 现象：ffprobe 报 `Invalid data` / `moov atom not found` 等。
- 处理：如实报告；提示用户重新导出或上传更常见格式。不编造时长/编码。

## 长视频抽帧超时

- wall-time 180s。
- 处理：用 `--ss/--t` 限片段，或 `--every` 拉大间隔。

## 转录为空

- `sandbox_transcribe_audio` 返回空/失败。
- 处理：先 `extract_audio.py -sr 16000 -ac 1` 归一化再重试；检查音量/静音（`media_info` 看音量信息）后如实说明。

## 音频无声道/静音

- 现象：转码后极短或全是静音。
- 处理：用 `media_info` 看流；若原文件本身无有效音频，告知用户。

## 输出已存在

- 需 `--overwrite`。

## 错误退出码

- 非零退出 = 失败；stderr 有 `{status:"error", error}`。不把部分产物当成功。
