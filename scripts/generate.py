#!/usr/bin/env python3
"""Offline image import fixtures. Python 3.9+, Pillow 10+."""
import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import shutil
from array import array
import sys
import time
import uuid
from collections import Counter

MAX_COUNT = 20000
MAX_BYTES = 10 * 1024**3
RESERVE = 2_000_000_000
META_RESERVE = 32 * 1024**2
REGULAR = [(256,256),(640,480),(800,600),(1280,720),(1920,1080),
           (1080,1920),(2048,2048),(3840,2160)]
SPECIAL = [(1,1),(1,4096),(4096,1),(8192,64),(64,8192),(257,113),(17,31)]
BASE_FORMATS = ['JPG','PNG','WEBP','BMP','GIF','TIFF','ICO']
PIL_FORMAT = {'JPG':'JPEG', 'PNG':'PNG','WEBP':'WEBP','BMP':'BMP',
              'GIF':'GIF','TIFF':'TIFF','ICO':'ICO','AVIF':'AVIF'}
EXT = {'JPG':'jpg','PNG':'png','WEBP':'webp','BMP':'bmp','GIF':'gif',
       'TIFF':'tiff','ICO':'ico','AVIF':'avif'}
BAD = ['empty','header','truncated','extension_mismatch','internal_crc','non_image']
FIELDS = ['id','path','true_format','width','height','bytes','category','anomaly',
          'validation','sha256','source_format','source_width','source_height',
          'attribute','target_size_status','attempts','error']


class Rejected(Exception):
    pass


def pillow():
    global Image, ImageDraw
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise Rejected('Missing Pillow. Use an existing Python with Pillow, or see README.md for isolated installation. No installation performed.') from exc


def config_path():
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/ck-image/config.json'
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'ck-image/config.json'
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'ck-image/config.json'


def read_config(path):
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('configuration must be an object')
        return value
    except (ValueError, OSError) as exc:
        raise Rejected(f'Cannot read configuration {path}: {exc}') from exc


def allocation(count, ratios):
    raw = [count * x / 100 for x in ratios]
    counts = [math.floor(x) for x in raw]
    for i in sorted(range(3), key=lambda i: (-(raw[i] - counts[i]), i))[:count-sum(counts)]:
        counts[i] += 1
    return counts


def numbers(text, n):
    try:
        result = [float(x) for x in text.split(',')]
        if len(result) != n or not all(math.isfinite(x) and x >= 0 for x in result):
            raise ValueError()
        return result
    except ValueError as exc:
        raise Rejected(f'Expected {n} finite nonnegative comma-separated numbers: {text}') from exc


def parse_sizes(text):
    if not text:
        return None
    try:
        sizes = [tuple(map(int, x.lower().split('x'))) for x in text.split(',')]
        if not sizes or any(len(s) != 2 or min(s) < 1 or max(s) > 16384 or s[0]*s[1] > 16777216 for s in sizes):
            raise ValueError()
        return sizes
    except ValueError as exc:
        raise Rejected('Invalid sizes; maximum side 16384, maximum pixels 16777216.') from exc


def guard(path, length, total, limit):
    if total + length > limit:
        raise Rejected('Batch image capacity limit reached; refusing further writes.')
    if shutil.disk_usage(path).free < RESERVE + META_RESERVE + length + 65536:
        raise Rejected('Insufficient disk space to retain 2 GB plus metadata reserve.')


def encode(im, fmt, quality=80, index=0):
    out = io.BytesIO()
    kw = {}
    if fmt in ('JPG','WEBP','AVIF'):
        kw['quality'] = quality
    if fmt == 'JPG':
        if im.mode not in ('RGB','L','CMYK'):
            im = im.convert('RGB')
        kw['progressive'] = bool(index % 2)
    if fmt == 'PNG':
        kw['compress_level'] = index % 7
    if fmt == 'WEBP':
        kw['method'] = 0
    if fmt == 'AVIF':
        kw['speed'] = 8
    if fmt == 'TIFF':
        kw['compression'] = 'raw'
    if fmt == 'ICO':
        kw['sizes'] = [im.size]
    im.save(out, format=PIL_FORMAT[fmt], **kw)
    return out.getvalue()


def available(requested):
    warnings, usable = [], []
    for fmt in requested:
        try:
            data = encode(Image.new('RGB', (32,32), 'red'), fmt)
            with Image.open(io.BytesIO(data)) as test:
                test.load()
                if test.format != PIL_FORMAT[fmt]:
                    raise ValueError('format mismatch')
            usable.append(fmt)
        except Exception as exc:
            if fmt != 'AVIF':
                raise Rejected(f'{fmt} encoder unavailable: {exc}') from exc
            warnings.append(f'AVIF unavailable, skipped: {exc}')
    if not usable:
        raise Rejected('No usable encoders. ' + '; '.join(warnings))
    if 'AVIF' not in requested:
        try:
            data = encode(Image.new('RGB',(8,8)), 'AVIF')
            with Image.open(io.BytesIO(data)) as test:
                test.load()
            warnings.append('AVIF available; enable with --formats ...,AVIF.')
        except Exception:
            warnings.append('AVIF unavailable; base formats unaffected.')
    return usable, warnings


def picture(size, index, rng, fmt, boundary, variant=0):
    w,h = size
    color = lambda: tuple(rng.randrange(256) for _ in range(3))
    style = (index + variant) % 4
    im = Image.new('RGB', size, color())
    if style == 1:
        a,b = color(),color()
        ramp = Image.new('RGB',(1,256))
        ramp.putdata([tuple((a[c]*(255-y)+b[c]*y)//255 for c in range(3)) for y in range(256)])
        im = ramp.resize(size)
    elif style == 2 or variant == 2:
        sw,sh = (w,h) if variant == 2 else (min(w,256),min(h,256))
        raw = rng.getrandbits(sw*sh*24).to_bytes(sw*sh*3,'little')
        im = Image.frombytes('RGB',(sw,sh),raw).resize(size)
    draw = ImageDraw.Draw(im)
    if style != 3:
        for j in range(12):
            x1,x2 = sorted([rng.randrange(w),rng.randrange(w)])
            y1,y2 = sorted([rng.randrange(h),rng.randrange(h)])
            if j % 2:
                draw.rectangle((x1,y1,x2,y2), fill=color())
            else:
                draw.ellipse((x1,y1,x2,y2), outline=color(), width=max(1,min(w,h)//100))
    attribute = 'rgb'
    if boundary:
        if fmt in ('PNG','WEBP','TIFF','ICO','AVIF') and index % 3 == 0:
            im = im.convert('RGBA')
            im.putalpha(Image.linear_gradient('L').resize(size))
            attribute = 'alpha_gradient'
        elif fmt == 'JPG' and index % 2:
            im = im.convert('CMYK')
            attribute = 'cmyk'
        elif fmt == 'PNG' and index % 3 == 1:
            gray = im.convert('L')
            words = array('H', (v*257 for v in gray.tobytes()))
            if sys.byteorder != 'little':
                words.byteswap()
            im = Image.frombytes('I;16',size,words.tobytes())
            attribute = 'gray16'
        elif fmt in ('PNG','GIF','BMP'):
            im = im.quantize(colors=16)
            attribute = 'palette16'
        else:
            im = im.convert('L')
            attribute = 'grayscale'
    if not boundary and min(size) >= 32:
        draw = ImageDraw.Draw(im)
        label = f'IMG_{index:06d}  {w}x{h}'
        draw.rectangle((0,0,min(w-1,260),17),fill='black')
        draw.text((3,3),label,fill='white')
    return im,attribute


def inspect(data, full=False):
    with Image.open(io.BytesIO(data)) as im:
        fmt, size = im.format, im.size
        im.verify()
    if full:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
    return fmt,size


def corrupt(data, kind):
    if kind == 'empty':
        return b''
    if kind == 'header':
        return b'BADHEADER' + data[9:]
    if kind == 'truncated':
        return data[:max(1,len(data)//3)]
    if kind == 'non_image':
        return b'This is intentionally not an image.\n'
    if kind == 'internal_crc':
        pos = 8
        damaged = bytearray(data)
        while pos + 12 <= len(data):
            length = int.from_bytes(data[pos:pos+4],'big')
            if data[pos+4:pos+8] == b'IDAT':
                damaged[pos+8+length] ^= 0xff
                return bytes(damaged)
            pos += length + 12
        raise ValueError('PNG IDAT missing')
    return data


def make_batch(base):
    for n in range(1,1000000):
        p = base / f'图片测试_批次{n:03d}'
        try:
            p.mkdir()
        except FileExistsError:
            continue
        (p/'01_正常与边界图片').mkdir()
        (p/'02_异常图片').mkdir()
        return p
    raise Rejected('No unused batch folder available.')


def run(args):
    cfgpath = Path(args.config).expanduser() if args.config else config_path()
    cfg = read_config(cfgpath)
    if args.show_config:
        print(json.dumps({'config_path':str(cfgpath), **cfg}, ensure_ascii=False))
        return 0
    if not 1 <= args.count <= MAX_COUNT:
        raise Rejected('count must be 1..20000.')
    if not math.isfinite(args.max_total_gib) or not 0 < args.max_total_gib <= 10:
        raise Rejected('max-total-gib must be > 0 and <= 10 GiB.')
    limit = int(args.max_total_gib * 1024**3)
    ratios = numbers(args.ratios,3)
    if not math.isclose(sum(ratios),100,abs_tol=1e-8,rel_tol=0):
        raise Rejected('ratios must sum to 100.')
    counts = allocation(args.count,ratios)
    target = numbers(args.target_kib,2) if args.target_kib else None
    if target and (target[0] > target[1] or target[1] == 0):
        raise Rejected('target-kib requires 0 <= minimum <= maximum, maximum > 0.')
    if target and target[0]*1024*sum(counts[:2]) > limit:
        raise Rejected('Requested minimum target sizes exceed the batch capacity limit.')
    if target and target[1]*1024 > MAX_BYTES:
        raise Rejected('Requested single-file size exceeds 10 GiB.')
    sizes = parse_sizes(args.sizes)
    requested = list(dict.fromkeys(x.strip().upper().replace('JPEG','JPG') for x in args.formats.split(',')))
    requested = ['TIFF' if x == 'TIF' else x for x in requested]
    if any(x not in PIL_FORMAT for x in requested):
        raise Rejected('Unknown format. Supported: ' + ','.join(PIL_FORMAT))
    if sizes and 'ICO' in requested and any(max(s) > 256 for s in sizes):
        raise Rejected('Explicit ICO sizes must be <= 256x256. Remove ICO or change sizes.')
    output = args.output or cfg.get('output')
    if not output:
        print('NEED_OUTPUT: Ask the user for a save directory, then use --output PATH --remember.')
        return 3
    if not isinstance(output,str):
        raise Rejected('Saved output path must be a string.')
    base = Path(output).expanduser().resolve()
    pillow()
    formats,warnings = available(requested)
    ancestor = base
    while not ancestor.exists():
        ancestor = ancestor.parent
    guard(ancestor,0,0,limit)
    base.mkdir(parents=True,exist_ok=True)
    if args.remember:
        cfgpath.parent.mkdir(parents=True,exist_ok=True)
        tmp = cfgpath.with_name(cfgpath.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with tmp.open('x',encoding='utf-8') as f:
                json.dump({'output':str(base)},f,ensure_ascii=False,indent=2)
            tmp.replace(cfgpath)
        finally:
            tmp.unlink(missing_ok=True)
    batch = make_batch(base)
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(2**63)
    config = {**vars(args),'output':str(base),'seed':seed,'effective_formats':formats,
              'category_counts':dict(zip(['normal','boundary','abnormal'],counts)),
              'hard_image_limit_bytes':limit,'disk_reserve_bytes':RESERVE,
              'python':sys.version,'pillow':__import__('PIL').__version__, 'warnings':warnings}
    (batch/'生成配置.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    rows, errors, total, stopped = [], [], 0, ''
    seen = set()
    started = time.monotonic()
    categories = ['normal']*counts[0]+['boundary']*counts[1]+['abnormal']*counts[2]
    positions = Counter()
    for index,category in enumerate(categories,1):
        pos = positions[category]
        positions[category] += 1
        fmt = formats[pos % len(formats)]
        kind = BAD[pos % len(BAD)] if category == 'abnormal' else ''
        if kind in ('internal_crc','extension_mismatch'):
            fmt = 'PNG'
        if sizes:
            size = sizes[pos % len(sizes)]
        elif fmt == 'ICO':
            size = [(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)][(pos//len(formats))%6]
        elif category == 'boundary' and pos % 2 == 0:
            size = SPECIAL[(pos//2)%len(SPECIAL)]
        else:
            size = REGULAR[(pos % len(formats) + pos//len(formats))%len(REGULAR)]
        ext = 'jpg' if kind == 'extension_mismatch' else EXT[fmt]
        name = f'IMG_{index:06d}_{ext.upper()}_{size[0]}x{size[1]}.{ext}'
        relative = Path('02_异常图片' if kind else '01_正常与边界图片')/name
        row = dict.fromkeys(FIELDS,'')
        row.update(id=f'IMG_{index:06d}',path=relative.as_posix(),category=category,anomaly=kind,
                   source_format=PIL_FORMAT[fmt],source_width=size[0],source_height=size[1])
        try:
            for attempt in range(3):
                try:
                    rng = random.Random(seed + index*1009 + attempt*1000003)
                    candidates = []
                    for variant in range(3 if target and not kind else 1):
                        im,attribute = picture(size,index,rng,fmt,category=='boundary',variant)
                        data = encode(im,fmt,rng.randint(40,95),index+variant)
                        im.close()
                        candidates.append((data,attribute))
                        if not target or target[0]*1024 <= len(data) <= target[1]*1024:
                            break
                    distance = lambda pair: max(target[0]*1024-len(pair[0]),0,len(pair[0])-target[1]*1024) if target else 0
                    data,attribute = min(candidates,key=distance)
                    full = fmt not in seen or index % 10 == 0
                    actual,actual_size = inspect(data,full)
                    if actual != PIL_FORMAT[fmt] or actual_size != size:
                        raise ValueError(f'Unexpected format/dimensions: {actual} {actual_size}')
                    status = 'verified+decoded' if full else 'verified'
                    if kind:
                        data = corrupt(data,kind)
                        actual,actual_size = 'UNRECOGNIZED',('','')
                        try:
                            with Image.open(io.BytesIO(data)) as probe:
                                actual,actual_size = probe.format,probe.size
                        except Exception:
                            pass
                        try:
                            inspect(data,True)
                            status = 'expected_anomaly:decoder_accepted'
                        except Exception as exc:
                            status = 'expected_anomaly:decoder_rejected:' + type(exc).__name__
                    row.update(true_format=actual,width=actual_size[0],height=actual_size[1],
                               attribute=attribute,validation=status,attempts=attempt+1,
                               target_size_status=('not_applicable' if kind or not target else
                               'hit' if target[0]*1024 <= len(data) <= target[1]*1024 else 'miss'))
                    break
                except Exception as exc:
                    errors.append({'id':row['id'],'attempt':attempt+1,'error':str(exc)[:200]})
                    if attempt == 2:
                        raise
            guard(batch,len(data),total,limit)
            dest = batch/relative
            created = False
            try:
                with dest.open('xb') as f:
                    created = True
                    f.write(data)
                written = dest.read_bytes()
                if written != data:
                    raise OSError('Read-back differs from encoded bytes')
                if not kind:
                    inspect(written, full)
            except OSError:
                if created:
                    dest.unlink(missing_ok=True)
                raise
            total += len(data)
            seen.add(fmt)
            row.update(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        except Rejected as exc:
            stopped = str(exc)
            break
        except OSError as exc:
            stopped = f'I/O failure: {exc}'
            row.update(validation='failed',error=str(exc)[:200])
            rows.append(row)
            break
        except Exception as exc:
            row.update(validation='failed',error=str(exc)[:200])
        rows.append(row)
    with (batch/'测试图片清单.csv').open('x',encoding='utf-8-sig',newline='') as f:
        writer = csv.DictWriter(f,fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    success = [r for r in rows if r['sha256']]
    failed = len(rows)-len(success)
    report = {'path':str(batch),'requested':args.count,'generated':len(success),
              'failed':failed,'not_generated':args.count-len(rows),'image_bytes':total,
              'categories':dict(Counter(r['category'] for r in success)),
              'formats':dict(Counter(r['true_format'] for r in success)),
              'target_misses':sum(r['target_size_status']=='miss' for r in success),
              'full_decoded_valid':sum(r['validation']=='verified+decoded' for r in success),
              'retry_events':errors,'stopped_reason':stopped,'warnings':warnings,
              'elapsed_seconds':round(time.monotonic()-started,3)}
    (batch/'生成报告.txt').write_text('图片导入测试生成报告\n'+json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    return 1 if failed or stopped else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count',type=int,default=500)
    parser.add_argument('--formats',default=','.join(BASE_FORMATS))
    parser.add_argument('--ratios',default='70,20,10',help='normal,boundary,abnormal; sum 100')
    parser.add_argument('--sizes',help='comma-separated WIDTHxHEIGHT')
    parser.add_argument('--output')
    parser.add_argument('--remember',action='store_true')
    parser.add_argument('--config',help='optional isolated config file')
    parser.add_argument('--show-config',action='store_true')
    parser.add_argument('--seed',type=int)
    parser.add_argument('--target-kib',help='approximate min,max KiB per valid image')
    parser.add_argument('--max-total-gib',type=float,default=10)
    try:
        return run(parser.parse_args())
    except (Rejected,OSError) as exc:
        print('REFUSED: '+str(exc),file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
