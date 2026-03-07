## yt-dlp

基于 `yt-dlp`，从 YouTube 等网站下载视频或音频。

- `ytdl <链接>`: 下载视频 (默认)
- `ytdl <关键词>`: 搜索 youtube 并返回候选列表（缓存 10 分钟）
- `ytdl <序号>`: 下载当前会话最近一次搜索结果中的对应视频
- `ytdl m <链接>`: 下载音频
- `ytdl m <关键词>`: 按音乐特征重排搜索结果并返回候选列表（缓存 10 分钟）
- `ytdl m <序号>`: 下载当前会话最近一次搜索结果中的对应音频
- `ytdl _proxy <url>`: 设置 HTTP/SOCKS 代理
- `ytdl _proxy`: 删除代理
- `ytdl _codec <codec>`: 设置优先选择的 YouTube 视频编码 (默认 avc1, 可选 vp9/av01)
- `ytdl _codec`: 删除优先选择的 YouTube 视频编码
- `ytdl update`: 更新 yt-dlp
