"""Write the private local comparison page from verified preview receipts."""
from pathlib import Path
import argparse
import json
import hashlib
import shutil
import math


def verified_loaded_benchmark(folder, scene_hash, denoised_folder):
    gpu = json.loads((folder/'runtime.json').read_text())
    cpu = json.loads((folder/'denoise-wall.json').read_text(encoding='utf-8-sig'))
    plan = json.loads((folder/'pieces.json').read_text())
    proof = json.loads((denoised_folder/'proof.json').read_text())
    png = denoised_folder/'denoised.png'
    png_hash = hashlib.sha256(png.read_bytes()).hexdigest()
    pieces = [{k:v for k,v in p.items() if k != 'reused'} for p in plan['pieces']]
    if (gpu.get('completed') is not True or gpu.get('sceneSha256') != scene_hash
            or gpu.get('windows') != 9 or gpu.get('samplesPerWindow') != 4
            or plan.get('sceneSha256') != scene_hash
            or proof.get('completed') is not True or proof.get('sceneSha256') != scene_hash
            or proof.get('samples') != 4 or proof.get('width') != 720 or proof.get('height') != 720
            or proof.get('pieces') != pieces or proof.get('sha256') != png_hash
            or cpu.get('pngSha256') != png_hash
            or not all(isinstance(v, (int,float)) and not isinstance(v,bool) and math.isfinite(v) and v > 0
                       for v in (gpu.get('completeBatchWallSeconds'), cpu.get('completeCpuJobWallSeconds')))):
        raise ValueError('Require matching completed nine-window GPU and denoised image receipts')
    return gpu, cpu

PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>GPU resolution comparison</title>
<style>
*{box-sizing:border-box}body{margin:0;color:#25282a;background:#fafafa;font:15px/1.5 system-ui,sans-serif}main{max-width:1480px;margin:auto;padding:24px 32px 60px}nav{display:flex;justify-content:space-between;gap:16px;font-size:13px}a{color:inherit}h1{font:36px/1.2 Georgia,serif;margin:18px 0 10px}h2{font:24px Georgia,serif;margin:28px 0 16px}p{color:#565f64;max-width:980px;margin:8px 0}header{border-bottom:1px solid #ccd1d4;padding-bottom:16px}.controls{display:flex;gap:20px;flex-wrap:wrap;align-items:end;padding:16px 0;border-bottom:1px solid #ccd1d4}label{display:flex;flex-direction:column;font-size:13px;gap:5px}select,button{font:inherit;color:inherit;border:1px solid #727c82;background:white;padding:8px;min-height:40px}select:focus-visible,button:focus-visible,input:focus-visible{outline:3px solid #176b97;outline-offset:2px}.gallery{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:26px 20px;margin-top:22px}figure{margin:0;min-width:0}.mount{border:1px solid #ccd1d4;background:#e8ebed;height:360px;overflow:auto}.plane{position:relative;margin:auto;width:var(--size,360px);height:var(--size,360px)}img{display:block;max-width:none;width:100%;height:100%;image-rendering:pixelated}.smooth img{image-rendering:auto}figcaption{padding-top:10px}strong{font:19px Georgia,serif}small{display:block;color:#565f64;font-size:12px}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;text-align:left}th,td{padding:10px 14px;border-bottom:1px solid #ccd1d4}th:first-child,td:first-child{padding-left:0}.scroll{overflow:auto}.pair{display:grid;grid-template-columns:minmax(300px,720px) 1fr;gap:28px;margin-top:20px}.pair .mount{height:600px}.wipe{position:absolute;inset:0;clip-path:inset(0 50% 0 0)}.divider{position:absolute;top:0;bottom:0;left:50%;width:2px;background:white;box-shadow:0 0 0 1px #333}.range{width:100%;margin:18px 0;min-height:32px}footer{font-size:12px;border-top:1px solid #ccd1d4;margin-top:30px;padding-top:12px;color:#565f64}@media(max-width:850px){.gallery{grid-template-columns:repeat(2,minmax(0,1fr))}.pair{grid-template-columns:1fr}}@media(max-width:540px){main{padding:16px}.gallery{grid-template-columns:1fr}h1{font-size:30px}.mount{height:340px}.pair .mount{height:390px}th,td{padding:8px;font-size:13px}}
.world #gallery .plane{width:min(var(--size),100%);height:auto;aspect-ratio:1}.world #gallery .mount{height:auto;aspect-ratio:1}
</style></head><body class="world"><main>
<header><nav><a href="/">Nuclear Option Map</a><a href="../sample-counts/">Sample comparison</a></nav><h1>Four samples. Six resolutions.</h1><p>Same 360 × 360 meter city patch, same scene and lighting. CUDA rendering on the RTX 4070 Ti SUPER, followed by CPU HDR denoising of each complete image.</p><p><b>Prototype materials:</b> this uses the existing cropped GPU scene with reduced scenery textures. It compares output resolution; it does not establish full-source texture quality. X 10,570 · Z 8,150.</p></header>
<div class="controls"><label>Image display<select id="display"><option value="world">Same world area · 360 px</option><option value="native">Actual output pixels · 1:1</option><option value="large">Same world area · 720 px</option></select></label><label>Enlargement<select id="filter"><option value="pixel">Show output pixels</option><option value="smooth">Smooth</option></select></label></div>
<p id="status" role="status">Loading previews…</p><div id="gallery" class="gallery"></div>
<h2>Compare detail</h2><div class="controls"><label>A · left<select id="a"></select></label><label>B · right<select id="b"></select></label><button id="swap">Swap</button></div><div class="pair"><div><div class="mount"><div class="plane" style="--size:720px"><img id="b-img" alt=""><div id="wipe" class="wipe"><img id="a-img" alt=""></div><div id="divider" class="divider"></div></div></div><label>Reveal A<input id="reveal" class="range" type="range" min="0" max="100" value="50"></label></div><p>Both sides show the same ground at the same display scale. Move the divider to compare roof edges, roads, trees, and shadows. The native-image links below each preview open the original PNG.</p></div>
<h2>Measured time and full-map extrapolation</h2><p>Preview time includes Blender startup, validation, all GPU pieces, assembly, CPU denoising, and PNG saving. It excludes preparing the already-existing scene. These are complete-image times.</p><div class="scroll"><table><thead><tr><th>m/pixel</th><th>Output</th><th>GPU pieces</th><th>Preview total</th><th>Heartland</th><th>Ignus</th></tr></thead><tbody id="times"></tbody></table></div><p><b>The map columns extrapolate the initial prototype method, including restarting Blender per piece.</b> They are not a production completion estimate. Production scene preparation, varied terrain complexity, tile encoding, and disk space are additional costs.</p><p id="loaded-worker"></p><p id="detail"></p><footer>Local private comparison · no live map replacement · all six images denoised</footer>
<script type="module">
const $=id=>document.getElementById(id);let rows=[];const seconds=n=>`${n.toFixed(2)} s`, hours=n=>`${(n/3600).toFixed(1)} h`;
function pair(){const a=rows.find(r=>String(r.scale)===$('a').value),b=rows.find(r=>String(r.scale)===$('b').value);for(const [key,row] of [['a',a],['b',b]]){$(`${key}-img`).src=row.nativePngUrl;$(`${key}-img`).alt=`${row.scale} meters per pixel, four samples, denoised`}reveal()}
function reveal(){$('wipe').style.clipPath=`inset(0 ${100-$('reveal').value}% 0 0)`;$('divider').style.left=`${$('reveal').value}%`}
function display(){document.body.classList.toggle('world',$('display').value==='world');document.querySelectorAll('#gallery .plane').forEach((plane,i)=>plane.style.setProperty('--size',`${$('display').value==='native'?rows[i].width:$('display').value==='large'?720:360}px`))}
try{const response=await fetch('comparison.json',{cache:'no-store'});if(!response.ok)throw Error(`HTTP ${response.status}`);const data=await response.json();rows=data.rows;if(!data.completed||rows.length!==6)throw Error('Comparison is incomplete');for(const row of rows){const fig=document.createElement('figure');fig.innerHTML=`<div class="mount"><div class="plane"><img alt="City at ${row.scale} meters per pixel, denoised" src="${row.nativePngUrl}"></div></div><figcaption><strong>${row.scale} m/pixel</strong><small>${row.width} × ${row.height} · ${seconds(row.completePreviewWallSeconds)} total · ${row.pieces} GPU ${row.pieces===1?'piece':'pieces'}</small><a href="${row.nativePngUrl}">Open native PNG</a></figcaption>`;$('gallery').append(fig);for(const key of ['a','b']){const option=new Option(`${row.scale} m/pixel`,row.scale);$(key).append(option)}const tr=document.createElement('tr');tr.innerHTML=`<td>${row.scale}</td><td>${row.width} × ${row.height}</td><td>${row.pieces}</td><td>${seconds(row.completePreviewWallSeconds)}</td><td>${hours(row.forecasts.Heartland.extrapolatedSeconds)}</td><td>${hours(row.forecasts.Ignus.extrapolatedSeconds)}</td>`;$('times').append(tr)}$('a').value='2.5';$('b').value='0.5';pair();display();$('status').hidden=true;$('detail').textContent='Full-map footprints: Heartland 81,920 × 81,920 m; Ignus 163,840 × 81,920 m. All estimates use the measured time for the same 360 m square.'}catch(e){$('status').textContent=`Could not load comparison: ${e.message}`}
$('display').onchange=display;$('filter').onchange=()=>document.body.classList.toggle('smooth',$('filter').value==='smooth');$('a').onchange=$('b').onchange=pair;$('reveal').oninput=reveal;$('swap').onclick=()=>{const a=$('a').value;$('a').value=$('b').value;$('b').value=a;pair()};
</script></main></body></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipts', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--loaded-worker', type=Path)
    parser.add_argument('--loaded-denoised', type=Path, help='Measured denoised output, default loaded-worker/denoised')
    args = parser.parse_args()
    data = json.loads(args.receipts.read_text())
    if not data.get('completed') or [r['scale'] for r in data['rows']] != [3, 2.5, 2, 1.5, 1, .5]:
        parser.error('Require all six completed resolutions')
    loaded_text = ''
    if args.loaded_worker:
        try:
            gpu, cpu = verified_loaded_benchmark(args.loaded_worker, data['sceneSha256'],
                                                  args.loaded_denoised or args.loaded_worker/'denoised')
        except ValueError as error:
            parser.error(str(error))
        total = gpu['completeBatchWallSeconds'] + cpu['completeCpuJobWallSeconds']
        data['loadedWorkerBenchmark'] = dict(scale=.5, completePreviewWallSeconds=total,
                                             gpuCompleteBatchWallSeconds=gpu['completeBatchWallSeconds'],
                                             cpuCompleteJobWallSeconds=cpu['completeCpuJobWallSeconds'])
        loaded_text = (f'Loaded-worker improvement measured at 0.5 m/pixel: {total:.2f} seconds for the complete denoised image '
                       f'({gpu["completeBatchWallSeconds"]:.2f} s for all nine GPU pieces plus {cpu["completeCpuJobWallSeconds"]:.2f} s for CPU assembly, denoising, and saving), '
                       f'compared with {data["rows"][-1]["completePreviewWallSeconds"]:.2f} seconds above. '
                       f'At that prototype rate, the same area extrapolation is {total*81920**2/360**2/3600:.1f} hours for Heartland '
                       f'and {total*2*81920**2/360**2/3600:.1f} hours for Ignus; production preparation and tiling remain additional.')
    args.output.mkdir(parents=True, exist_ok=True)
    for row in data['rows']:
        source = Path(row['pngPath'])
        if hashlib.sha256(source.read_bytes()).hexdigest() != row['sha256']:
            parser.error('Preview PNG hash differs from receipt')
        name = 'mpp-' + str(row['scale']).replace('.', '_') + '.png'
        shutil.copyfile(source, args.output/name)
        row['nativePngUrl'] = name
        row.pop('pngPath')
    (args.output/'comparison.json').write_text(json.dumps(data, indent=2))
    (args.output/'index.html').write_text(PAGE.replace('<p id="loaded-worker"></p>',
                                                     '<p id="loaded-worker">'+loaded_text+'</p>'), encoding='utf-8')


if __name__ == '__main__':
    main()
