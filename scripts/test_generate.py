#!/usr/bin/env python3
"""Offline regression checks; writes only below the required --output directory."""
import argparse
import contextlib
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from PIL import Image

SCRIPT = Path(__file__).with_name('generate.py')
spec = importlib.util.spec_from_file_location('generator',SCRIPT)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    args = parser.parse_args()
    root = Path(args.output).resolve()
    root.mkdir(parents=True,exist_ok=False)
    config = root/'isolated-config.json'
    results = []

    def invoke(name, extra, code=0, output=True):
        command = [sys.executable,'-X','utf8',str(SCRIPT),'--config',str(config),'--seed','12345']
        if output:
            command += ['--output',str(root/'batches')]
        command += extra
        p = subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
        assert p.returncode == code, (name,p.returncode,p.stdout,p.stderr)
        results.append({'test':name,'passed':True,'exit_code':p.returncode})
        print(name, 'PASS',flush=True)
        return p.stdout

    def validate(name, text, count):
        report = json.loads(text)
        batch = Path(report['path'])
        rows = list(csv.DictReader((batch/'测试图片清单.csv').open(encoding='utf-8-sig',newline='')))
        assert len(rows) == count == report['generated']
        assert report['failed'] == report['not_generated'] == 0
        assert len({r['id'] for r in rows}) == count
        assert len({r['path'] for r in rows}) == count
        assert json.loads((batch/'生成配置.json').read_text(encoding='utf-8'))['seed'] == 12345
        stored = json.loads((batch/'生成报告.txt').read_text(encoding='utf-8').split('\n',1)[1])
        assert stored == report
        assert sum(int(r['bytes']) for r in rows) == report['image_bytes']
        for r in rows:
            file = batch/r['path']
            data = file.read_bytes()
            assert hashlib.sha256(data).hexdigest() == r['sha256']
            assert len(data) == int(r['bytes'])
            if r['category'] != 'abnormal':
                with Image.open(file) as im:
                    im.load()  # independent full decode of EVERY valid test artifact
                    assert im.format == r['true_format'] == r['source_format']
                    assert im.size == (int(r['width']),int(r['height']))
                    assert im.size == (int(r['source_width']),int(r['source_height']))
            elif r['anomaly'] == 'empty':
                assert not data
            elif r['anomaly'] == 'extension_mismatch':
                with Image.open(file) as im:
                    assert file.suffix == '.jpg' and im.format == 'PNG'
                    im.load()
            elif r['anomaly'] == 'internal_crc':
                try:
                    with Image.open(file) as im:
                        im.verify()
                except (SyntaxError,OSError):
                    pass
                else:
                    raise AssertionError('Damaged CRC was not detected')
        results.append({'test':name+' artifacts','passed':True,'files':count,'path':str(batch)})
        return rows

    invoke('first-use requires directory',['--count','1'],3,False)
    rows = validate('10 JPG',invoke('10 JPG',['--count','10','--formats','JPG','--ratios','100,0,0']),10)
    assert {r['true_format'] for r in rows} == {'JPEG'}
    rows = validate('20 mixed',invoke('20 mixed',['--count','20']),20)
    assert {r['true_format'] for r in rows if r['category'] != 'abnormal'} == set(g.PIL_FORMAT[x] for x in g.BASE_FORMATS)
    sizes = '1x1,256x256,640x480,800x600,1280x720,1920x1080,1080x1920,2048x2048,3840x2160,8192x64'
    rows = validate('10 resolutions',invoke('10 resolutions',['--count','10','--formats','PNG','--ratios','100,0,0','--sizes',sizes]),10)
    assert len({(r['width'],r['height']) for r in rows}) == 10
    rows = validate('10 abnormal',invoke('10 abnormal',['--count','10','--ratios','0,0,100']),10)
    assert {r['anomaly'] for r in rows} == set(g.BAD)
    assert all(r['validation'].startswith('expected_anomaly:') for r in rows)
    assert g.allocation(500,[70,20,10]) == [350,100,50]
    for n in range(1,20001):
        assert sum(g.allocation(n,[70,20,10])) == n
    rows = validate('500 default',invoke('500 default',[]),500)
    assert [sum(r['category']==c for r in rows) for c in ('normal','boundary','abnormal')] == [350,100,50]
    rows = validate('70 boundary',invoke('70 boundary',['--count','70','--ratios','0,100,0']),70)
    assert {'alpha_gradient','gray16','cmyk','palette16','grayscale'} <= {r['attribute'] for r in rows}
    for name, extra in [
        ('count overflow',['--count','20001']),
        ('capacity overflow',['--max-total-gib','10.01']),
        ('target minimum overflow',['--count','20000','--ratios','100,0,0','--target-kib','1024,2048']),
        ('invalid ratios',['--ratios','80,20,10']),
        ('nonfinite capacity',['--max-total-gib','nan']),
        ('invalid ICO size',['--formats','ICO','--sizes','640x480']),
        ('oversized pixels',['--sizes','16384x16384'])]:
        invoke(name,extra,2)
    text = invoke('runtime capacity stop',['--count','10','--formats','BMP','--ratios','100,0,0','--max-total-gib','0.00001'],1)
    report = json.loads(text)
    assert report['image_bytes'] <= int(0.00001*1024**3)
    assert report['generated'] == 0 and report['not_generated'] == 10
    validate('remember directory',invoke('remember directory',['--count','1','--formats','PNG','--remember']),1)
    validate('reuse directory',invoke('reuse directory',['--count','1','--formats','PNG'],output=False),1)
    validate('size target',invoke('size target',['--count','3','--formats','JPG','--ratios','100,0,0','--target-kib','2,50']),3)
    # Simulated low-space filesystem must reject before any image write.
    with patch.object(g.shutil,'disk_usage',return_value=type('Usage',(),{'free':g.RESERVE-1})()):
        try:
            g.guard(root,0,0,g.MAX_BYTES)
        except g.Rejected:
            results.append({'test':'2 GB reserve refusal (simulated free space)','passed':True})
        else:
            raise AssertionError('Low disk space not refused')
    g.pillow()
    original = g.encode
    def no_avif(im,fmt,*a,**kw):
        if fmt == 'AVIF':
            raise OSError('simulated missing AVIF encoder')
        return original(im,fmt,*a,**kw)
    with patch.object(g,'encode',side_effect=no_avif):
        usable,warnings = g.available(['PNG','AVIF'])
        assert usable == ['PNG'] and warnings
        results.append({'test':'AVIF fallback (simulated missing codec)','passed':True})
    usable,_ = g.available(['AVIF','PNG'])
    if 'AVIF' in usable:
        validate('AVIF available',invoke('AVIF available',['--count','2','--formats','AVIF','--ratios','100,0,0']),2)
    # Bounded retries: encoder probe succeeds; actual images fail persistently.
    attempts = []
    def broken(im,fmt,*a,**kw):
        if im.size == (123,123):
            attempts.append(1)
            raise ValueError('injected fixture generation error')
        return original(im,fmt,*a,**kw)
    argv = [str(SCRIPT),'--output',str(root/'retry'),'--config',str(config),
            '--count','1','--formats','PNG','--sizes','123x123','--ratios','100,0,0']
    with patch.object(g,'encode',side_effect=broken), patch.object(sys,'argv',argv), contextlib.redirect_stdout(io.StringIO()) as stream:
        assert g.main() == 1
    retry_report = json.loads(stream.getvalue())
    assert len(attempts) == 3 and retry_report['failed'] == 1 and len(retry_report['retry_events']) == 3
    results.append({'test':'bounded retry and failure report (injected failure)','passed':True})
    result = {'python':sys.version,'pillow':__import__('PIL').__version__,
              'platform':sys.platform,'checks':len(results),'results':results,
              'limitations':['No macOS host available; no native Mac test.',
                             'Codex picker invocation requires a refreshed user session; CLI and policy tested.']}
    (root/'test-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':len(results),'results':str(root/'test-results.json')},ensure_ascii=False))


if __name__ == '__main__':
    main()
