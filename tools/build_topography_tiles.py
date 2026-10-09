# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.3.4", "pillow==12.3.0", "numba==0.62.1"]
# ///
"""CPU-only bounded native-ground elevation/relief tile producer; no scene renderer."""
import argparse,json,math
from pathlib import Path
import numpy as np
from PIL import Image
from build_terrain_tiles import tile_index,raster_tile

def northwest_shade(ground,pixel_size):
    """Map rows point south, columns east; illuminate from northwest at 45 degrees."""
    valid=np.isfinite(ground)
    center=np.where(valid,ground,0)
    # Unknown neighbors use this sample, never a fictitious zero-height coast wall.
    west=np.roll(center,1,axis=1); east=np.roll(center,-1,axis=1)
    north=np.roll(center,1,axis=0); south=np.roll(center,-1,axis=0)
    west=np.where(np.roll(valid,1,axis=1),west,center);east=np.where(np.roll(valid,-1,axis=1),east,center)
    north=np.where(np.roll(valid,1,axis=0),north,center);south=np.where(np.roll(valid,-1,axis=0),south,center)
    west[:,0]=center[:,0];east[:,-1]=center[:,-1];north[0]=center[0];south[-1]=center[-1]
    dx=(east-west)/(2*pixel_size);dy=(south-north)/(2*pixel_size)
    illumination=(.5*dx+.5*dy+math.sqrt(.5))/np.sqrt(1+dx*dx+dy*dy)
    return np.rint(np.clip(illumination,0,1)*255).astype(np.uint8)

def pack_ground(ground,shade,step):
    valid=np.isfinite(ground)&(ground>0)
    codes=np.rint(np.where(valid,ground,0)/step).clip(0,65535).astype(np.uint16)
    valid=valid&(codes>0)
    rgba=np.zeros((*ground.shape,4),dtype=np.uint8)
    rgba[:,:,0]=codes>>8;rgba[:,:,1]=codes&255;rgba[:,:,2]=shade;rgba[:,:,3]=valid*255
    rgba[~valid]=0
    return rgba

def run(args):
    info=json.loads((args.cache/'geometry.json').read_text())
    if info.get('schemaVersion')!=4:raise ValueError('Expected native ground geometry cache schema 4')
    vertices=np.load(args.cache/'vertices.npy',mmap_mode='r');faces=np.load(args.cache/'faces.npy',mmap_mode='r');classes=np.load(args.cache/'classes.npy',mmap_mode='r')
    item=next(m for m in json.loads(args.manifest.read_text(encoding='utf-8-sig'))['maps']if m['id']==info['map'])
    width,depth=item['widthMeters'],item['heightMeters'];tile_size=256
    base=tile_size*max(1,round(width/depth));height=round(base*depth/width)
    maximum=info['maxHeightMeters'];step=max(.01,maximum/65535);levels=[]
    args.output.mkdir(parents=True,exist_ok=True)
    for level in range(args.levels):
        w,h=base*2**level,height*2**level
        if max(w,h)>8192:raise ValueError('Topography generation bounded to 8192 pixels per axis')
        cols,rows=math.ceil(w/tile_size),math.ceil(h/tile_size)
        # Same map-space index used by the existing native-ground cartography.
        pointers,ids=tile_index(vertices,faces,width,depth,cols,rows)
        pixel=width/w
        for y in range(rows):
            for x in range(cols):
                cw,ch=min(tile_size,w-x*tile_size),min(tile_size,h-y*tile_size)
                shape=(ch+6,cw+6);ground=np.full(shape,-np.inf,dtype=np.float32)
                heights=np.full(shape,-np.inf,dtype=np.float32);categories=np.zeros(shape,dtype=np.uint8)
                candidates=[]
                for yy in range(max(0,y-1),min(rows,y+2)):
                    for xx in range(max(0,x-1),min(cols,x+2)):
                        i=yy*cols+xx;candidates.append(ids[pointers[i]:pointers[i+1]])
                selected=np.unique(np.concatenate(candidates));selected=selected[(classes[selected]==1)|(classes[selected]==4)]
                raster_tile(vertices,faces,classes,selected,heights,categories,ground,-width/2+(x*tile_size-3)*pixel,depth/2-(y*tile_size-3)*pixel,pixel)
                ground[~np.isfinite(ground)]=np.nan
                packed=pack_ground(ground,northwest_shade(ground,pixel),step)[1:-1,1:-1]
                target=args.output/str(level)/str(x)/f'{y}.png';target.parent.mkdir(parents=True,exist_ok=True);Image.fromarray(packed).save(target)
        levels.append(dict(level=level,width=w,height=h,columns=cols,rows=rows,urlTemplate=f'{args.output.name}/{level}/{{x}}/{{y}}.png'))
        print(f'Completed native-ground topography level {level}: {w}x{h}',flush=True)
    descriptor=dict(tiles=dict(tileSize=tile_size,gutter=2,levels=levels),minHeightMeters=0,heightStepMeters=step,maxHeightMeters=maximum)
    (args.output/'descriptor.json').write_text(json.dumps(descriptor,indent=2))
    (args.cache/'topography-provenance.json').write_text(json.dumps(dict(source=info['sourceScene'],sourceSceneSHA256=info['sourceSceneSHA256'],geometrySchema=4,groundClasses=[1,4],waterAndUnknown='transparent; no synthesized heights',heightQuantizationMeters=step,illumination='NW azimuth 315 degrees, altitude 45 degrees',descriptor=descriptor),indent=2))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--cache',type=Path,required=True);parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--levels',type=int,default=4)
    args=parser.parse_args()
    if not 1<=args.levels<=5:parser.error('levels must be 1..5')
    run(args)






