"""处理样板目录 raw 中的副本。裁切坐标为左、上、右、下，右下不含。"""
import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageStat


def edge_depth(image):
    """只接受连续、近乎单色且有清晰内界的窄边。"""
    width, height = image.size
    if min(width, height) < 40:
        return 0
    reference = None
    for y in range(17):
        row = image.crop((17, y, width-17, y+1))
        stat = ImageStat.Stat(row)
        if reference is None:
            reference = stat.mean
        if max(stat.stddev) > 3 or max(abs(a-b) for a,b in zip(stat.mean,reference)) > 5:
            changed = sum(max(abs(c-r) for c,r in zip(p,reference)) > 15 for p in (row.getpixel((x, 0)) for x in range(row.width)))
            return y if y >= 2 and changed > row.width * .85 else 0
    return 0


def auto_box(im):
    t = Image.Transpose
    return [edge_depth(im.transpose(t.TRANSPOSE)), edge_depth(im),
            im.width-edge_depth(im.transpose(t.ROTATE_90)),
            im.height-edge_depth(im.transpose(t.FLIP_TOP_BOTTOM))]


def dhash(image):
    pixels = image.convert('L').resize((9,8), Image.Resampling.LANCZOS).tobytes()
    bits = [pixels[y*9+x] > pixels[y*9+x+1] for y in range(8) for x in range(8)]
    return format(int(''.join(str(int(b)) for b in bits),2),'016x')


def ingest(root, min_width):
    raw = root/'raw'
    dest = root/'assets/4k'
    dest.mkdir(exist_ok=True, parents=True)
    selection = root/'selection.json'
    rules = json.loads(selection.read_text()) if selection.exists() else {}
    rows, swatches = [], []
    for path in sorted(raw.iterdir()):
        if path.suffix.lower() not in {'.png','.jpg','.jpeg','.webp'}:
            continue
        name = path.stem
        rule = rules.get(name,{})
        im = Image.open(path).convert('RGB')
        box = rule.get('crop_box') or auto_box(im)
        l,t,r,b = box
        if not (0 <= l < r <= im.width and 0 <= t < b <= im.height):
            raise ValueError(f'无效裁切框 {name}')
        crop = im.crop(box)
        digest = dhash(crop)
        reason = rule.get('exclude')
        row = dict(id=name, raw='raw/'+path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                   dhash=digest, size=list(im.size), crop_box=box, adopted=not reason, reason=reason,
                   candidates=[a['id'] for a in rows if (int(a['dhash'],16)^int(digest,16)).bit_count() <= 6],
                   output=None, output_size=None, scale=1)
        rows.append(row)
        if reason:
            continue
        target = (max(2160/min(crop.size),2996/crop.height) if name=='color-plan' else min_width/crop.width)
        scale = 1 if target<=1 else 2 if target<=2 else 4
        while scale < target:
            scale *= 4
        output = dest/(name+'.png')
        with tempfile.TemporaryDirectory(dir=dest) as work:
            stage = Path(work)/'in.png'
            crop.save(stage)
            factor = 1
            while factor < scale:
                # x4plus 的网络原生倍率为四倍。两倍交付从四倍结果采样。
                enhanced = Path(work)/f'x{factor*4}.png'
                subprocess.run([str(Path.home()/'.local/bin/realesrgan-ncnn-vulkan'),'-i',str(stage),'-o',str(enhanced),'-s','4','-n','realesrgan-x4plus'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                stage = enhanced
                factor *= 4
            size = (crop.width*scale,crop.height*scale)
            with Image.open(stage) as result:
                if result.size != (crop.width*factor,crop.height*factor):
                    raise ValueError(f'模型倍率异常 {name}')
                if factor != scale:
                    result.resize(size,Image.Resampling.LANCZOS).save(stage.with_name('final.png'))
                    stage = stage.with_name('final.png')
            stage.replace(output)
        row.update(output=output.relative_to(root).as_posix(), output_size=list(size),scale=scale)
        if name!='color-plan':
            swatches.append(crop.resize((64,64)))
        print(name,size,flush=True)
    if not swatches:
        raise ValueError('请至少采用一张效果图或材质图，以便提取色板。')
    sheet = Image.new('RGB',(64,64*len(swatches)))
    for i,im in enumerate(swatches):
        sheet.paste(im,(0,i*64))
    quant = sheet.quantize(5).convert('RGB')
    palette = ['#%02X%02X%02X'%rgb for count,rgb in sorted(quant.getcolors(),reverse=True)]
    (root/'ingest-report.json').write_text(json.dumps(dict(assets=rows,palette=palette),indent=2,ensure_ascii=False))


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--min-width',type=int,default=3600)
    args = parser.parse_args()
    ingest(args.root,args.min_width)
