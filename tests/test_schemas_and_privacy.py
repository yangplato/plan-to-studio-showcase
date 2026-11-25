import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest

try:
    from jsonschema import Draft202012Validator
    from PIL import Image, ImageDraw
    from scripts.ingest_assets import auto_box
    from scripts.verify_brochure import verify
except ModuleNotFoundError as exc:
    raise SystemExit(f"缺少 {exc.name}。请在仓库虚拟环境安装 requirements.txt 后运行测试。") from None

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT/'examples/courtyard-105-sample'
CASE = ROOT/'local/showcase-105'
BLIND = ROOT/'local/blind-test-95'


def read(path):
    return json.loads(path.read_text())


def public():
    paths = subprocess.check_output(['git','ls-files','-co','--exclude-standard','-z'],cwd=ROOT)
    return [ROOT/p.decode() for p in paths.split(b'\0') if p]


class Contracts(unittest.TestCase):
    def test_all_public_json(self):
        schemas = {p.name.split('.')[0]: read(p) for p in (ROOT/'schemas').glob('*.json')}
        for schema in schemas.values():
            Draft202012Validator.check_schema(schema)
        paths = [p for p in public() if p.suffix=='.json' and p.parent.name!='schemas' and p.name!='measure.config.json']
        paths += [p for p in CASE.glob('*.json') if p.stem in schemas]
        paths += [p for p in BLIND.glob('*.json') if p.stem in schemas]
        for path in paths:
            with self.subTest(file=path.name):
                Draft202012Validator(schemas[path.name.split('.')[0]]).validate(read(path))

    def test_references_and_timeline(self):
        manifest = read(EXAMPLE/'case-manifest.example.json')
        script = read(EXAMPLE/'script.example.json')
        assets = {a['id'] for a in manifest['assets'] if a['adopted']}
        self.assertEqual(len(manifest['assets']),len({a['id'] for a in manifest['assets']}))
        self.assertIn(manifest['color_plan'],assets)
        pages = {p['id'] for p in script['pages']}
        self.assertEqual(len(pages),10)
        for item in manifest['facts']+script['pages']+script['shots']:
            self.assertTrue(set(item['image_ids'])<=assets)
        cursor = 0
        for shot in script['shots']:
            self.assertIn(shot['page_id'],pages)
            for cue in shot['narration']:
                self.assertEqual(cursor,cue['start_ms'])
                self.assertEqual(cue['text'],''.join(b['text'] for b in cue['subtitles']))
                for block in cue['subtitles']:
                    self.assertEqual(cursor,block['start_ms'])
                    self.assertGreater(block['end_ms'],cursor)
                    cursor=block['end_ms']
                self.assertEqual(cursor,cue['end_ms'])
        self.assertEqual(cue['text'],read(EXAMPLE/'brand.json')['closing'])

    def test_local_files(self):
        if not CASE.exists():
            self.skipTest('本地样板可选')
        m=read(CASE/'case-manifest.json')
        self.assertEqual(m,read(EXAMPLE/'case-manifest.example.json'))
        self.assertEqual(read(CASE/'script.json'),read(EXAMPLE/'script.example.json'))
        for item in m['assets']:
            self.assertEqual(hashlib.sha256((CASE/item['raw']).read_bytes()).hexdigest(),item['sha256'])
            if item['adopted']:
                with Image.open(CASE/item['output']) as im:
                    self.assertEqual(list(im.size),item['output_size'])
                    if item['id']==m['color_plan']:
                        self.assertGreaterEqual(min(im.size),2160)
                        self.assertGreaterEqual(im.height,2996)
                    else:
                        self.assertGreaterEqual(im.width,3600)

    def test_sample_copy(self):
        script = read(EXAMPLE/'script.example.json')
        for page, shot in zip(script['pages'],script['shots']):
            self.assertTrue(85 <= len(page['body']) <= 108)
            for cue in shot['narration']:
                self.assertNotIn(cue['text'],page['body'])
                self.assertNotRegex(cue['text'],r'近景|镜头|画面左侧|审图')
        self.assertEqual(script['shots'][1]['image_ids'],['color-plan'])

    def test_exported_brochures(self):
        output = CASE/'brochures'
        if not output.exists():
            self.skipTest('未生成本地实案画册')
        for slug in ('atelier-warm','swiss-grid','nocturne-gallery','editorial-archive'):
            with self.subTest(template=slug):
                totals = verify(output/slug, read(CASE/'script.json'))
                self.assertGreaterEqual(totals['text'],20)
                self.assertEqual(totals['photos'],{'editorial-archive':17,'nocturne-gallery':11}.get(slug,12))
        for i in range(1,11):
            with Image.open(output/'comparison-4k'/f'p{i:02d}.jpg') as im:
                self.assertEqual(im.size,(3840,2160))

    def test_brand_artwork(self):
        brand = read(EXAMPLE/'brand.json')
        self.assertEqual(brand, read(ROOT/'assets/brands/atelier-qimu/brand.json'))
        for variant in brand['logos'].values():
            self.assertTrue((ROOT/variant['svg']).is_file())
            with Image.open(ROOT/variant['png']) as logo:
                self.assertEqual(logo.size, (1024,1024))
                self.assertEqual(logo.getchannel('A').getextrema(), (0,255))

    def test_narrow_border(self):
        image=Image.new('RGB',(120,100),'white')
        ImageDraw.Draw(image).rectangle((6,4,117,89),fill='#806049')
        self.assertEqual(auto_box(image),[6,4,118,90])
        wall=Image.new('RGB',(120,100),'ivory')
        self.assertEqual(auto_box(wall),[0,0,120,100])

    def test_privacy(self):
        # 指纹保留审计能力，避免将客户词表带入公开仓库。
        fingerprints = {2: ['051d1e3a46e0a4bebf5fea7ae516c24ab31810aec209e597a45fe31c618cfa80', '88de17f82a31eb50ff6af5004a1df10ee6aa67fbb221cc3a206fba7bb8000d1e', '60d39163f23698c030e04a13fd7f584bf0e9b9a2c292d1d8343091193b02f639', 'd2a30f484ca8d0dddc2160f7a8570fee0422bb888d10aaee11cdd4cadde91203', '317248c4aa8d609d54620400626958d618bde27cb90e57392b2f2cf12a654f54', 'f77767fe5de7277186dd401a9ccc1691a8f05f1374fd689ae4fe64b1f3f5917f', '217a60a2a316803d7c4bf156e478dd9b7f6c9d07e52befe636a15312339a24fe', '3a60b4463606f7240386461eed4873be520bfee9db84b676d7f7537585b85560', 'f4b2d14b4fca78fd8112d49de4f97cc4baabe24095c314659379acd106101f25', 'd120ac8165114e0103c2e5f0127262e23ebbc43048e9d51dd2b1e8fe73f64ac9'], 3: ['c04b490e94be53d99ff6ed9539c825f56ba1691e4084f54d941a2e96f52263de', 'da3404adcfe8f28d8d3895781056e3ca4a53ee1e0751dc7a47deeea5b3f29ad0', '33a0e8700964bcceb26198a8a34116ead732b2b302d7959e5420331e1c39f851', 'a6f487c5459e9086169a2fd253ffb715403f2fc16b8faf6fe1e59489fc2b684e', 'df33f45d6450b2c049a0a7616f4e7f4801df7abe832b3815bba87dfdd2aee987'], 6: ['30f6f96d1122ba2db6a01cde8aa69a5bfb457cd9a917b2beecedb4e58fa841be']}
        private_prefix = b'/'+b'Users'+b'/'
        paths = public() + list(CASE.rglob('*.json')) + list(CASE.rglob('*.md'))
        paths += list(BLIND.glob('*.json')) + list(BLIND.glob('*.md'))
        for path in paths:
            data=path.relative_to(ROOT).as_posix().encode()+b'\n'+path.read_bytes()
            if path.parent == ROOT / "assets/readme" and sys.platform == "darwin":
                visible = subprocess.check_output(["osascript", "-l", "JavaScript",
                    str(ROOT / "tests/read_image_text.js"), str(path)], timeout=150)
                self.assertGreater(len(visible.strip()), 30, path.name)
                data += b'\n' + visible
            self.assertFalse(private_prefix in data,msg=path.name)
            with self.subTest(file=path.name):
                self.assertFalse(re.search(rb'(?:sk-|eyJ)[A-Za-z0-9_.-]{24,}',data))
                text=data.decode('utf-8','ignore').lower()
                for word in re.findall(r'[\u3400-\u9fff]+|[a-z]+',text):
                    for length,digests in fingerprints.items():
                        for i in range(len(word)-length+1):
                            digest=hashlib.sha256(word[i:i+length].encode()).hexdigest()
                            self.assertNotIn(digest,digests)
