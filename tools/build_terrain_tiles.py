# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.3.4", "numba==0.62.1", "pillow==12.3.0"]
# ///
"""Spatially indexed native geometry tiles; never allocates a whole fine raster."""
import argparse
import json
import math
import hashlib
import time
from pathlib import Path
import numpy as np
from numba import njit
from PIL import Image,ImageDraw,ImageFont
from raster_terrain import identity,local_matrix,read_obj,colorize
from terrain_selection import ground_surface_record,building_surface_record,road_surface_record


def obj_face_slots(path):
    """UnityPy OBJ group order corresponds to serialized renderer material slots."""
    slots=[];slot=-2
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            parts=line.split()
            if not parts:continue
            if parts[0]=="g":slot+=1
            elif parts[0]=="f":slots.extend([max(0,slot)]*(len(parts)-3))
    return np.asarray(slots,dtype=np.int32)


def pavement_materials(report_path,map_id):
    if not report_path:return {}
    report=json.loads(report_path.read_text(encoding="utf-8-sig"));result={}
    for item in report["maps"]:
        if item["mapId"]!=map_id:continue
        for obj in item["renderedObjects"]:
            meshes=[c["mesh"] for c in obj["components"]if "mesh"in c]
            renderers=[c for c in obj["components"]if "materials"in c]
            if not meshes or not renderers:continue
            mesh=meshes[0];key=(mesh["assetFile"],mesh["pathId"])
            names=[m.get("name","")for m in renderers[0]["materials"]]
            result[key]=names
    return result


def cache_geometry(scene_path,map_id,cache_dir,material_report=None):
    cache_dir.mkdir(parents=True,exist_ok=True)
    receipt=cache_dir/"geometry.json"
    if receipt.exists():
        info=json.loads(receipt.read_text())
        if info.get("schemaVersion")==4 and info["map"]==map_id and info.get("sourceSceneSHA256")==hashlib.sha256(scene_path.read_bytes()).hexdigest() and info.get("materialReportSHA256")== (hashlib.sha256(material_report.read_bytes()).hexdigest()if material_report else None):
            return info
    scene=json.loads(scene_path.read_text())
    records=[r for r in scene["objects"] if r["map"]==map_id]
    transforms={identity(r):r for r in records if r["type"]=="Transform"}
    matrices={}
    def world(ref,chain=()):
        key=identity(ref)
        if key in chain:raise ValueError("Transform cycle")
        if key not in matrices:
            t=transforms[key];m=local_matrix(t["transform"])
            matrices[key]=world(t["parent"],chain+(key,))@m if t["parent"] else m
        return matrices[key]
    selected=[r for r in records if "mesh"in r and (ground_surface_record(r) or building_surface_record(r)or road_surface_record(r))]
    if not selected:raise ValueError("No ground/building surfaces found")
    all_vertices,all_faces,all_classes,provenance=[],[],[],[]
    mesh_cache={};offset=0;materials=pavement_materials(material_report,map_id)
    for index,r in enumerate(selected):
        filename=r["file"]
        if Path(filename).name!=filename:raise ValueError("Unsafe mesh filename")
        if filename not in mesh_cache:mesh_cache[filename]=read_obj(scene_path.parent/filename)
        vertices,faces=mesh_cache[filename]
        if not len(vertices) or not len(faces):continue
        matrix=world(r["transform"])
        transformed=(vertices@matrix[:3,:3].T+matrix[:3,3]).astype(np.float32)
        all_vertices.append(transformed);all_faces.append(faces+offset)
        category=2 if building_surface_record(r) else 3 if road_surface_record(r) else 1
        face_classes=np.full(len(faces),category,dtype=np.uint8)
        names=materials.get((r["mesh"]["assetFile"],r["mesh"]["pathId"]),[])
        if category==1 and names:
            slots=obj_face_slots(scene_path.parent/filename)
            if len(slots)!=len(faces):raise ValueError("OBJ submesh face count mismatch")
            for slot,name in enumerate(names):
                if name.lower()in("asphalt","runway1","road_2lane","asphalt_dirtedge"):
                    face_classes[slots==slot]=4
        all_classes.append(face_classes)
        provenance.append({"name":r["name"],"mesh":r["mesh"],"transform":r["transform"],
                           "category":"building" if category==2 else "road" if category==3 else "ground","triangles":len(faces)})
        offset+=len(vertices)
        if index%256==0:print(f"Cached {index+1}/{len(selected)} instances",flush=True)
    vertices=np.concatenate(all_vertices);faces=np.concatenate(all_faces);classes=np.concatenate(all_classes)
    np.save(cache_dir/"vertices.npy",vertices,allow_pickle=False)
    np.save(cache_dir/"faces.npy",faces,allow_pickle=False)
    np.save(cache_dir/"classes.npy",classes,allow_pickle=False)
    info={"schemaVersion":4,"map":map_id,"instances":len(provenance),"vertices":len(vertices),"triangles":len(faces),
          "materialReportSHA256":hashlib.sha256(material_report.read_bytes()).hexdigest()if material_report else None,
          "pavementTriangles":int((classes==4).sum()),
          "sourceSceneSHA256":hashlib.sha256(scene_path.read_bytes()).hexdigest(),
          "maxHeightMeters":float(vertices[:,1].max()),"sourceScene":str(scene_path.resolve()),"sources":provenance}
    receipt.write_text(json.dumps(info,indent=2))
    return info


@njit(cache=True)
def tile_index(vertices,faces,width,depth,columns,rows):
    side=width/columns
    bounds=np.empty((len(faces),4),dtype=np.int32)
    counts=np.zeros(columns*rows,dtype=np.int64)
    for i in range(len(faces)):
        a,b,c=vertices[faces[i,0]],vertices[faces[i,1]],vertices[faces[i,2]]
        xmin,xmax=min(a[0],b[0],c[0]),max(a[0],b[0],c[0])
        zmin,zmax=min(a[2],b[2],c[2]),max(a[2],b[2],c[2])
        left,right=max(0,math.floor((xmin+width*.5)/side)),min(columns-1,math.floor((xmax+width*.5)/side))
        top,bottom=max(0,math.floor((depth*.5-zmax)/side)),min(rows-1,math.floor((depth*.5-zmin)/side))
        bounds[i]=left,right,top,bottom
        for y in range(top,bottom+1):
            for x in range(left,right+1):counts[y*columns+x]+=1
    pointers=np.empty(len(counts)+1,dtype=np.int64);pointers[0]=0
    for i in range(len(counts)):pointers[i+1]=pointers[i]+counts[i]
    ids=np.empty(pointers[-1],dtype=np.int32);cursor=pointers[:-1].copy()
    for i in range(len(faces)):
        left,right,top,bottom=bounds[i]
        for y in range(top,bottom+1):
            for x in range(left,right+1):
                cell=y*columns+x;ids[cursor[cell]]=i;cursor[cell]+=1
    return pointers,ids


@njit(cache=True)
def contour_segments(ground,interval):
    segments=[]
    points=np.empty((4,2),dtype=np.float64)
    for y in range(ground.shape[0]-1):
        for x in range(ground.shape[1]-1):
            a,b,c,d=ground[y,x],ground[y,x+1],ground[y+1,x+1],ground[y+1,x]
            if not(np.isfinite(a)and np.isfinite(b)and np.isfinite(c)and np.isfinite(d)):continue
            low,high=min(a,b,c,d),max(a,b,c,d)
            start=max(1,math.ceil(low/interval))
            for k in range(start,math.floor(high/interval)+1):
                elevation=k*interval;count=0
                values=(a,b,c,d);xs=(x,x+1,x+1,x);ys=(y,y,y+1,y+1)
                for edge in range(4):
                    nxt=(edge+1)%4;v1,v2=values[edge],values[nxt]
                    if(v1>=elevation)!=(v2>=elevation):
                        t=(elevation-v1)/(v2-v1)
                        points[count,0]=xs[edge]+t*(xs[nxt]-xs[edge])
                        points[count,1]=ys[edge]+t*(ys[nxt]-ys[edge]);count+=1
                if count==2:
                    segments.append((points[0,0],points[0,1],points[1,0],points[1,1],elevation))
                elif count==4:
                    # Bilinear-center asymptotic decision for saddle cells.
                    if(a+b+c+d)*.25>=elevation:
                        segments.append((points[0,0],points[0,1],points[1,0],points[1,1],elevation))
                        segments.append((points[2,0],points[2,1],points[3,0],points[3,1],elevation))
                    else:
                        segments.append((points[0,0],points[0,1],points[3,0],points[3,1],elevation))
                        segments.append((points[1,0],points[1,1],points[2,0],points[2,1],elevation))
    return segments


def stitch_contours(segments,precision=4):
    adjacency={};ends=[]
    for i,(x1,y1,x2,y2,elevation)in enumerate(segments):
        a=(elevation,round(float(x1),precision),round(float(y1),precision));b=(elevation,round(float(x2),precision),round(float(y2),precision))
        ends.append((a,b))
        adjacency.setdefault(a,[]).append(i);adjacency.setdefault(b,[]).append(i)
    used=set();paths=[]
    order=sorted(range(len(ends)),key=lambda i:min(len(adjacency[ends[i][0]]),len(adjacency[ends[i][1]])))
    for first in order:
        if first in used:continue
        a,b=ends[first];node=a if len(adjacency[a])==1 else b if len(adjacency[b])==1 else a
        points=[(node[1],node[2])];elevation=node[0];edge=first
        while edge not in used:
            used.add(edge);a,b=ends[edge];node=b if node==a else a
            points.append((node[1],node[2]))
            remaining=[i for i in adjacency[node]if i not in used]
            if not remaining:break
            edge=remaining[0]
        paths.append((elevation,points))
    return paths


@njit(cache=True)
def tin_contours(vertices,faces,classes,ids,interval,bounds):
    maximum=0
    for index in ids:
        if classes[index]!=1 and classes[index]!=4:continue
        a,b,c=vertices[faces[index,0]],vertices[faces[index,1]],vertices[faces[index,2]]
        low,high=min(a[1],b[1],c[1]),max(a[1],b[1],c[1])
        if high>low:maximum+=max(0,math.floor(high/interval)-max(1,math.ceil(low/interval))+1)
    result=np.empty((maximum,5),dtype=np.float64);written=0
    points=np.empty((2,2),dtype=np.float64)
    left,south,right,north=bounds
    for index in ids:
        if classes[index]!=1 and classes[index]!=4:continue
        tri=vertices[faces[index]];low,high=min(tri[0,1],tri[1,1],tri[2,1]),max(tri[0,1],tri[1,1],tri[2,1])
        if high<=low:continue
        for k in range(max(1,math.ceil(low/interval)),math.floor(high/interval)+1):
            elevation=k*interval;count=0
            for edge in range(3):
                a,b=tri[edge],tri[(edge+1)%3]
                if(a[1]>=elevation)!=(b[1]>=elevation):
                    t=(elevation-a[1])/(b[1]-a[1])
                    if count<2:
                        points[count,0]=a[0]+t*(b[0]-a[0]);points[count,1]=a[2]+t*(b[2]-a[2])
                    count+=1
            if count!=2:continue
            x1,z1,x2,z2=points[0,0],points[0,1],points[1,0],points[1,1]
            dx,dz=x2-x1,z2-z1;t0,t1=0.,1.;valid=True
            ps=(-dx,dx,-dz,dz);qs=(x1-left,right-x1,z1-south,north-z1)
            for j in range(4):
                p,q=ps[j],qs[j]
                if abs(p)<1e-15:
                    if q<0:valid=False;break
                else:
                    r=q/p
                    if p<0:t0=max(t0,r)
                    else:t1=min(t1,r)
            if valid and t0<t1 and dx*dx+dz*dz>1e-12:
                result[written]=(x1+t0*dx,z1+t0*dz,x1+t1*dx,z1+t1*dz,elevation);written+=1
    return result[:written]


def simplify_path(points,tolerance):
    array=np.asarray(points,dtype=np.float64);keep={0,len(points)-1};stack=[(0,len(points)-1)]
    while stack:
        first,last=stack.pop()
        if last-first<=1:continue
        a,b=array[first],array[last];difference=b-a;norm=float(difference@difference)
        middle=array[first+1:last]
        if norm:
            t=np.clip((middle-a)@difference/norm,0,1)
            distance=np.sum((middle-a-t[:,None]*difference)**2,axis=1)
        else:distance=np.sum((middle-a)**2,axis=1)
        offset=int(np.argmax(distance));maximum=float(distance[offset])
        if maximum>tolerance*tolerance:
            index=first+1+offset;keep.add(index);stack.extend(((first,index),(index,last)))
    return [points[i]for i in sorted(keep)]


def export_contours(vertices,faces,classes,ids,bounds,interval,pixel_size,path,map_id,level,x,y):
    segments=tin_contours(vertices,faces,classes,ids,float(interval),np.asarray(bounds,dtype=np.float64))
    paths=[]
    for elevation,points in stitch_contours(segments,precision=1):
        if len(points)<2:continue
        simplified=simplify_path(points,pixel_size*.5)
        # Preserve tile clipping endpoints; only remove screen-invisible bends.
        payload=f"{map_id}:{interval}:{level}:{x}:{y}:{elevation}:{points[0]}:{points[-1]}".encode()
        paths.append({"id":hashlib.blake2b(payload,digest_size=8).hexdigest(),"elevationMeters":float(elevation),
                      "isIndex":round(elevation/interval)%5==0,"points":simplified})
    result={"schemaVersion":1,"bounds":list(bounds),"paths":paths}
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,separators=(",",":")))
    return result


def topographic_image(ground,categories,interval):
    valid=np.isfinite(ground);land=valid&(ground>=0)
    rgb=np.empty((*ground.shape,3),dtype=np.uint8);rgb[:]=(200,225,232)
    rgb[land]=(246,244,233)
    rgb[(categories==2)&(~valid|(ground>=0))]=(181,180,169)
    rgb[(categories==3)&land]=(219,216,207)
    image=Image.fromarray(rgb);draw=ImageDraw.Draw(image)
    paths=stitch_contours(contour_segments(ground,interval))
    for elevation,path in paths:
        index=round(elevation/interval)%5==0
        draw.line(path,fill=(133,120,99)if index else(179,164,141),width=2 if index else 1)
    # Labels only on long index contours, with a minimum screen spacing.
    candidates=[]
    for elevation,path in paths:
        if round(elevation/interval)%5:continue
        length=sum(math.hypot(b[0]-a[0],b[1]-a[1])for a,b in zip(path,path[1:]))
        if length>=70:candidates.append((length,elevation,path))
    placed=[];font=ImageFont.load_default(size=11)
    for _,elevation,path in sorted(candidates,reverse=True):
        x,y=path[len(path)//2]
        if not(35<x<image.width-35 and 20<y<image.height-20):continue
        if any(math.hypot(x-a,y-b)<120 for a,b in placed):continue
        text=f"{int(elevation)} m";box=draw.textbbox((x,y),text,font=font,anchor="mm")
        draw.rectangle((box[0]-2,box[1]-1,box[2]+2,box[3]+1),fill=(246,244,233))
        draw.text((x,y),text,font=font,anchor="mm",fill=(106,91,72));placed.append((x,y))
        if len(placed)>=4:break
    # Keep roofs readable and prevent contour ink from crossing roof footprints.
    pixels=np.asarray(image).copy();pixels[categories==2]=(181,180,169)
    return Image.fromarray(pixels),paths


@njit(cache=True)
def raster_tile(vertices,faces,classes,ids,heights,categories,ground,left,north,pixel_size):
    rows,columns=heights.shape
    for index in ids:
        face=faces[index];a,b,c=vertices[face[0]],vertices[face[1]],vertices[face[2]]
        ax,ay=(a[0]-left)/pixel_size-.5,(north-a[2])/pixel_size-.5
        bx,by=(b[0]-left)/pixel_size-.5,(north-b[2])/pixel_size-.5
        cx,cy=(c[0]-left)/pixel_size-.5,(north-c[2])/pixel_size-.5
        denominator=(by-cy)*(ax-cx)+(cx-bx)*(ay-cy)
        if abs(denominator)<1e-12:continue
        x0,x1=max(0,math.ceil(min(ax,bx,cx))),min(columns-1,math.floor(max(ax,bx,cx)))
        y0,y1=max(0,math.ceil(min(ay,by,cy))),min(rows-1,math.floor(max(ay,by,cy)))
        for row in range(y0,y1+1):
            for column in range(x0,x1+1):
                wa=((by-cy)*(column-cx)+(cx-bx)*(row-cy))/denominator
                wb=((cy-ay)*(column-cx)+(ax-cx)*(row-cy))/denominator;wc=1-wa-wb
                if wa>=-1e-8 and wb>=-1e-8 and wc>=-1e-8:
                    h=wa*a[1]+wb*b[1]+wc*c[1]
                    if (classes[index]==1 or classes[index]==4) and h>ground[row,column]:ground[row,column]=h
                    if h>heights[row,column]:
                        heights[row,column]=h;categories[row,column]=classes[index]


def render_tile(vertices,faces,classes,pointers,ids,x,y,columns,rows,width,depth,
                logical_width,logical_height,max_height,contour,tile_size=512,land_mask=None):
    halo=2;pixel_size=width/logical_width
    core_width=min(tile_size,logical_width-x*tile_size)
    core_height=min(tile_size,logical_height-y*tile_size)
    left=-width*.5+x*tile_size*pixel_size-halo*pixel_size
    north=depth*.5-y*tile_size*pixel_size+halo*pixel_size
    heights=np.full((core_height+2*halo,core_width+2*halo),-np.inf,dtype=np.float32)
    categories=np.zeros(heights.shape,dtype=np.uint8)
    ground=np.full(heights.shape,-np.inf,dtype=np.float32)
    relevant=[]
    for yy in range(max(0,y-1),min(rows,y+2)):
        for xx in range(max(0,x-1),min(columns,x+2)):
            cell=yy*columns+xx;relevant.append(ids[pointers[cell]:pointers[cell+1]])
    if relevant:
        candidates=np.unique(np.concatenate(relevant))
        raster_tile(vertices,faces,classes,candidates,heights,categories,ground,left,north,pixel_size)
    heights[~np.isfinite(heights)]=np.nan
    ground[~np.isfinite(ground)]=np.nan
    rgb=np.empty((*heights.shape,3),dtype=np.uint8);rgb[:]=(200,225,232)
    if land_mask is not None:
        # Conservative visual-only source mask. Never fills ground elevations.
        xs=left+(np.arange(heights.shape[1])+.5)*pixel_size
        zs=north-(np.arange(heights.shape[0])+.5)*pixel_size
        ix=np.clip(((xs/width+.5)*land_mask.shape[1]).astype(int),0,land_mask.shape[1]-1)
        iy=np.clip(((.5-zs/depth)*land_mask.shape[0]).astype(int),0,land_mask.shape[0]-1)
        authored_land=land_mask[np.ix_(iy,ix)]>=240
        rgb[authored_land&~np.isfinite(ground)]=(246,244,233)
    rgb[np.isfinite(ground)&(ground>=0)]=(246,244,233)
    rgb[(categories==3)&(heights>=0)]=(219,216,207)
    rgb[(categories==4)&(heights>=0)]=(219,216,207)
    rgb[(categories==2)&(heights>=0)]=(181,180,169)
    core=rgb[halo:halo+core_height,halo:halo+core_width,:3]
    c=categories[halo:halo+core_height,halo:halo+core_width]
    return Image.fromarray(core),int((c==1).sum()),int((c==2).sum())


def run(args):
    info=cache_geometry(args.scene,args.map,args.cache,args.material_report)
    vertices=np.load(args.cache/"vertices.npy",mmap_mode="r")
    faces=np.load(args.cache/"faces.npy",mmap_mode="r")
    classes=np.load(args.cache/"classes.npy",mmap_mode="r")
    item=next(m for m in json.loads(args.manifest.read_text(encoding="utf-8-sig"))["maps"]if m["id"]==args.map)
    width,depth=item["widthMeters"],item["heightMeters"]
    if not all(math.isfinite(v)and v>0 for v in(width,depth)):raise ValueError("Invalid map dimensions")
    base_width=512*max(1,round(width/depth))
    base_height=round(base_width*depth/width)
    levels=[];args.output.mkdir(parents=True,exist_ok=True)
    contour_levels=[]
    intervals=[int(v)for v in args.intervals.split(",")]
    land_mask=np.asarray(Image.open(args.land_mask).convert("L"))if args.land_mask else None
    def vectors(level,x,y,columns,rows,w,h,pointers,ids):
        cell=y*columns+x;candidate=ids[pointers[cell]:pointers[cell+1]]
        side=width/columns
        bounds=(-width*.5+x*side,depth*.5-(y+1)*side,-width*.5+(x+1)*side,depth*.5-y*side)
        output={}
        for interval in intervals:
            target=args.output/"contours"/str(interval)/str(level)/str(x)/f"{y}.json"
            if args.reuse_contours and target.exists():output[interval]=json.loads(target.read_text())
            else:output[interval]=export_contours(vertices,faces,classes,candidate,bounds,interval,width/w,target,args.map,level,x,y)
        return output
    previous_path=args.output/"tiles.json"
    previous=json.loads(previous_path.read_text())if previous_path.exists()else None
    for level in range(0 if args.preview_only else args.max_level+1):
        started=time.monotonic()
        w,h=base_width*2**level,base_height*2**level
        columns,rows=math.ceil(w/512),math.ceil(h/512)
        pointers,ids=tile_index(vertices,faces,width,depth,columns,rows)
        grounded,built=0,0
        for y in range(rows):
            for x in range(columns):
                target=args.output/"tiles"/str(level)/str(x)/f"{y}.webp"
                image,g,b=render_tile(vertices,faces,classes,pointers,ids,x,y,columns,rows,width,depth,w,h,
                                      info["maxHeightMeters"],args.contour,land_mask=land_mask)
                grounded+=g;built+=b
                target.parent.mkdir(parents=True,exist_ok=True)
                image.save(target,"WEBP",lossless=True,method=1)
                vectors(level,x,y,columns,rows,w,h,pointers,ids)
            if y%8==0:print(f"Level {level}: row {y+1}/{rows}",flush=True)
        levels.append({"level":level,"width":w,"height":h,"columns":columns,"rows":rows,
                       "urlTemplate":f"terrain/{args.map}/tiles/{level}/{{x}}/{{y}}.webp"})
        contour_levels.append({"level":level,"columns":columns,"rows":rows,
                               "urlTemplate":f"terrain/{args.map}/contours/{{interval}}/{level}/{{x}}/{{y}}.json"})
        if level<=1:
            overview=Image.new("RGB",(w,h),(25,57,74))
            for y in range(rows):
                for x in range(columns):
                    with Image.open(args.output/"tiles"/str(level)/str(x)/f"{y}.webp")as tile:
                        overview.paste(tile,(x*512,y*512))
            overview.save(args.output/"overview.webp","WEBP",lossless=True,method=1)
        contract={"image":f"terrain/{args.map}/overview.webp","tiles":{"tileSize":512,"levels":levels},
                  "contours":{"intervals":intervals,"levels":contour_levels}}
        if previous and previous.get("contours",{}).get("intervals")==intervals:
            for field in ("tiles","contours"):
                contract[field]["levels"]+= [v for v in previous[field]["levels"]if v["level"]>level]
        temporary=args.output/"tiles.json.tmp"
        temporary.write_text(json.dumps(contract,indent=2));temporary.replace(args.output/"tiles.json")
        private={"map":args.map,"completedLevel":level,"groundPixels":grounded,"buildingPixels":built,
                 "visualWaterFill":"opaque light blue independently of height coverage", "geometry":{k:v for k,v in info.items()if k!="sources"},
                 "style":"flat neutral ground, neutral buildings/roads; independent exact TIN contour vectors",
                 "visualLandFallback":str(args.land_mask.resolve())if args.land_mask else None,
                 "visualLandThreshold":240 if args.land_mask else None,
                "limitations":"Ground and projected roof cartography; pavement from serialized material slot triangles; texture markings not recovered; uncovered elevations remain unknown"}
        (args.cache/"tiles-provenance.json").write_text(json.dumps(private,indent=2))
        print(f"Completed level {level} ({w}x{h}, {columns*rows} tiles) in {time.monotonic()-started:.1f}s",flush=True)
    if args.preview_tile:
        level,x,y=map(int,args.preview_tile.split("/"));w,h=base_width*2**level,base_height*2**level
        columns,rows=math.ceil(w/512),math.ceil(h/512)
        if not(0<=x<columns and 0<=y<rows):raise ValueError("Preview tile outside map grid")
        pointers,ids=tile_index(vertices,faces,width,depth,columns,rows)
        image,_,_=render_tile(vertices,faces,classes,pointers,ids,x,y,columns,rows,width,depth,w,h,info["maxHeightMeters"],args.contour,land_mask=land_mask)
        results=vectors(level,x,y,columns,rows,w,h,pointers,ids)
        result=results[intervals[0]];draw=ImageDraw.Draw(image);left,south,right,north=result["bounds"]
        candidates=[]
        for path in result["paths"]:
            points=[((p[0]-left)/(right-left)*image.width,(north-p[1])/(north-south)*image.height)for p in path["points"]]
            draw.line(points,fill=(133,120,99)if path["isIndex"]else(179,164,141),width=2 if path["isIndex"]else 1)
            length=sum(math.hypot(b[0]-a[0],b[1]-a[1])for a,b in zip(points,points[1:]))
            if path["isIndex"]and length>70:candidates.append((length,path["elevationMeters"],points))
        placed=[];font=ImageFont.load_default(size=12)
        for _,elevation,points in sorted(candidates,reverse=True):
            px,py=points[len(points)//2]
            if not(35<px<image.width-35 and 20<py<image.height-20)or any(math.hypot(px-a,py-b)<120 for a,b in placed):continue
            text=f"{int(elevation)} m";box=draw.textbbox((px,py),text,font=font,anchor="mm")
            draw.rectangle((box[0]-2,box[1]-1,box[2]+2,box[3]+1),fill=(246,244,233))
            draw.text((px,py),text,font=font,anchor="mm",fill=(106,91,72));placed.append((px,py))
            if len(placed)>=4:break
        image.save(args.cache/f"topographic-preview-{level}-{x}-{y}.png")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene",required=True,type=Path)
    parser.add_argument("--manifest",required=True,type=Path)
    parser.add_argument("--map",required=True)
    parser.add_argument("--cache",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--max-level",default=6,type=int)
    parser.add_argument("--contour",default=100,type=float)
    parser.add_argument("--intervals",default="20,50,100")
    parser.add_argument("--preview-tile")
    parser.add_argument("--preview-only",action="store_true",help="Generate only the requested private close-up without changing completed pyramid metadata")
    parser.add_argument("--land-mask",type=Path)
    parser.add_argument("--material-report",type=Path)
    parser.add_argument("--reuse-contours",action="store_true")
    args=parser.parse_args()
    if not 0<=args.max_level<=7 or not math.isfinite(args.contour)or args.contour<=0:
        parser.error("Max level must be0..7 and contour must be finite positive")
    try:
        intervals=[int(v)for v in args.intervals.split(",")]
    except ValueError:parser.error("Intervals must be comma-separated positive integers")
    if not intervals or any(v not in(20,50,100)for v in intervals)or len(set(intervals))!=len(intervals):
        parser.error("Intervals must be distinct values from20,50,100")
    if args.preview_tile:
        try:
            preview=tuple(int(v)for v in args.preview_tile.split("/"))
        except ValueError:parser.error("Preview tile must be LEVEL/X/Y")
        if len(preview)!=3 or not 0<=preview[0]<=7 or min(preview[1:])<0:
            parser.error("Preview tile must be bounded LEVEL/X/Y")
    run(args)
