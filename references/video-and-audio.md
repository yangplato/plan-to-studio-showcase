# 配音、母带与 4K 成片

从仓库根目录激活虚拟环境。准备 FFmpeg、ffprobe 和中文宋体、黑体。系统找不到中文字体时，分别设置 SHOWCASE_SERIF 和 SHOWCASE_SANS 为可读的字体文件。实案目录包含 case-manifest.json、script.json 和素材表引用的图片。

```sh
CASE=local/showcase-105
python scripts/synthesize_voice.py --case "$CASE" --provider edge
python scripts/synthesize_voice.py --case "$CASE" --provider hailuo
python scripts/render_video.py --case "$CASE" --mode edge
python scripts/render_video.py --case "$CASE" --mode hailuo
python scripts/render_video.py --case "$CASE" --mode tv
python scripts/verify_video.py --case "$CASE"
python scripts/build_review.py --case "$CASE"
```

打开案例目录的「审阅总览.html」。素材区可展开 12 组原图和 UHD 审阅图。原生、超分和补足 UHD 的倍率分别标注。短边或长边不足 UHD 时，审阅器另存一份等比 Lanczos 放大图，保留已核验的素材原件。画册区提供十页同页对照与 PDF、PPTX。三台播放器使用本地 MP4，展厅播放器自动循环。

## 语音与字幕

每个 narration.text 调用一次服务。微软默认云扬，参数为 -12%、-4Hz、+0%。可用 `--voice zh-CN-XiaoxiaoNeural` 切换晓晓，也可用 `--brand` 指定品牌配置。语速等负值可用 `--rate=-12%`。字幕按词起止时间生成。字幕分块须完整拼成原句，且切点必须位于词间。

海螺使用 speech-2.8-hd 和 Chinese_gravelly_storyteller_nv1，默认 speed 为 1.08。请通过国内平台创建密钥，用 MINIMAX_API_KEY 或用户配置文件提供。返回的音频以 48 kHz 单声道解码。静音检测给出可选的气口，再按分块字数匹配。没有可用气口时按字数估时。海螺字幕为估算对齐，试听后可局部调整 timeline.json。

两轨均保留服务返回的整句音频。另加片头 650 ms、同镜句间 250 ms、跨镜 400 ms、片尾 2800 ms。每个 provider 单独保存缓存、实测时间轴、SRT、WAV、口播稿和发布文案。缓存按服务、模型或音色、参数与原文计算 SHA-256，读取时复核音频内容哈希。再次执行相同命令可离线重建可用缓存内的句子。海螺按账户计费。

## 图像与母带

影片以 3840 × 2160、30 fps 合成。客厅、主卧等六个单图镜头缓推至 1.038 倍。彩平保持稳镜。双图之间留出 48 像素。实景字幕使用底部渐变与双层文字阴影，纸底字幕直接用墨色。镜间以 14 帧叠化。展厅版时长 72 秒，空间题签持续 3.5 秒。末帧复用首帧的编码图像。讲解版直接读取 audio 下的 voice.wav 和 timeline.json。渲染阶段不调用配音服务。片头将整张静物照片水平翻转，花瓶落在右侧亮区。从第二镜起，右下角保持明暗自适应的品牌组合。两条讲解片最后一句品牌口播起声时切入深橄榄落版。

采样母带随仓库分发。缺少母带时使用 Python 按 ABC 内容合成泛音替代音色。可单独运行 `python scripts/compose_music.py score.wav`。语音与室内乐先校准至 −18 dBFS RMS。配乐以 −7.5 dB 为起点，根据 10 ms 人声能量帧，在 −38 至 −18 dBFS 门限之间平滑避让最多 6.5 dB，起控 80 ms、释放 450 ms。末尾 1.2 秒淡出。复核报告同时测量分轨和最终 AAC。展厅母带对齐整段乐曲并交叉叠化 2 秒。两版经两遍 loudnorm 处理后编码 AAC，再从最终 MP4 测量 EBU R128。

## 单独换画与复核

```sh
python scripts/update_video_picture.py --case "$CASE" --mode edge
python scripts/verify_video.py --case "$CASE"
```

可选模式为 edge、hailuo、tv。更新前读原片，保留现有编码音频，只替换视频。音频 SHA-256、流时长和帧数核对通过后替换原片。检查报告在 videos/video-qa-report.json，原分辨率抽帧在 video-checks 内。浏览器打开审阅页后，分别检查字幕入点、收尾和展厅自动回环。目标电视的解码和 U 盘循环行为需在实际设备确认。
