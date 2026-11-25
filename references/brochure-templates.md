# 四套画册的选用与返修

| 模板 | 适用场景 | 核心版式 |
|---|---|---|
| atelier-warm 暖木画刊 | 自然木色、生活方式提案 | 橄榄色书衣首尾呼应。暖纸彩平衔接满幅全景、错落双图和居中器物图版。 |
| swiss-grid 瑞士建筑网格 | 现代极简、讲解空间组织 | 版心用十二栏，栏距 150 像素，沟宽 24 像素。全册用现代黑体，配大号窄笔画数字和黑色封底栏。 |
| nocturne-gallery 暗夜展厅 | 意式深木、低照度展厅 | 满幅静物封面与黄铜细线展签。彩平外围透明，暗底透出柔光。 |
| editorial-archive 营造档案 | 中古、原木、材料说明 | 棉纸底印微型页眉与图版号。封面与第九页的可编辑色卡取自同一组实景采样。 |

## 运行

安装 requirements.txt，并准备 Chrome 与 measure-cjk。导出器优先使用 CHROME_BIN，其次查找系统 Chrome 或 Chromium。命令从 Skill 根目录运行。assets-dir 可以指向案例根目录或已处理的图片目录。

```sh
case_dir=local/showcase-105
.venv/bin/python scripts/build_brochure.py \
  --manifest "$case_dir/case-manifest.json" --script "$case_dir/script.json" \
  --brand assets/brands/atelier-qimu/brand.json --assets-dir "$case_dir" \
  --out-dir "$case_dir/brochures" --template all
.venv/bin/python scripts/verify_brochure.py --out-dir "$case_dir/brochures" --script "$case_dir/script.json"
measure-cjk lint "$case_dir"/brochures/*/*.html
```

将 all 换成表中的英文模板名可以只出一册。十页中第二页引用完整彩平，第四、九页各取两图，其余页各取一图。公开示例提供脱敏配置，使用者另备对应图片。修改版式时编辑 templates/brochure/layouts.py，各图框采用左上角坐标及宽高。

已有完整画册时，增加 `--only-page p04` 可更新该页 PNG 和对应的四版对照图。HTML、PDF、PPTX 均重新装订十页。其他页面也改动时应重出整册。每个案例使用独立输出目录。

## 查看与交付

打开「模板对比总览」切换同页四版和单册纵览。每张 4K 对照图由四张 1920 × 1080 预览按原尺寸拼成。单册 HTML 与同目录 media 文件夹一起保存，可离线打开，PDF 保留文字与高分辨率照片。Chrome 的 PDF 纸张为 1920 × 1080 CSS 像素，折合 20 × 11.25 英寸。现场如需与 PPTX 等大印刷，可等比缩印至 13⅓ × 7.5 英寸。印厂的出血、色彩配置与打样另行确认。

PPTX 页面为 16:9。图框按 288 像素/英寸取样；上采样素材的真实细节仍取决于原图。照片可替换，正文整段可改。换机开稿时先检查中文字体替换后的换行。字体按系统调用。正文、上标、徽标、色卡与细线分别保留为原生对象。
