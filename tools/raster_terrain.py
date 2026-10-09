# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.3.4", "numba==0.62.1", "pillow==12.3.0"]
# ///
"""Rasterize actual transformed base terrain triangles without a graphics runtime."""
import argparse
import json
import math
from pathlib import Path
import numpy as np
from numba import njit
from PIL import Image
from terrain_selection import terrain_record,ground_surface_record


def identity(ref):
    return ref["assetFile"], ref["pathId"]


def local_matrix(tree):
    p, q, scale = tree["m_LocalPosition"], tree["m_LocalRotation"], tree["m_LocalScale"]
    x, y, z, w = q["x"], q["y"], q["z"], q["w"]
    length = math.sqrt(x*x+y*y+z*z+w*w)
    if not length:
        raise ValueError("Invalid zero serialized quaternion")
    x, y, z, w = x/length, y/length, z/length, w/length
    rotation = np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])
    result = np.eye(4)
    result[:3, :3] = rotation @ np.diag([scale["x"], scale["y"], scale["z"]])
    result[:3, 3] = [p["x"], p["y"], p["z"]]
    return result


def read_obj(path):
    vertices, faces = [], []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "v":
                x, y, z = map(float, parts[1:4])
                vertices.append((-x, y, z))
            elif parts[0] == "f":
                indices = [int(v.split("/")[0])-1 for v in parts[1:]]
                for i in range(1, len(indices)-1):
                    faces.append((indices[0], indices[i], indices[i+1]))
    return np.asarray(vertices, dtype=np.float64), np.asarray(faces, dtype=np.int32)


@njit(cache=True)
def rasterize(vertices, faces, heights, width, depth):
    rows, columns = heights.shape
    for face in faces:
        a, b, c = vertices[face[0]], vertices[face[1]], vertices[face[2]]
        ax, ay = (a[0]/width+.5)*columns-.5, (.5-a[2]/depth)*rows-.5
        bx, by = (b[0]/width+.5)*columns-.5, (.5-b[2]/depth)*rows-.5
        cx, cy = (c[0]/width+.5)*columns-.5, (.5-c[2]/depth)*rows-.5
        denominator = (by-cy)*(ax-cx)+(cx-bx)*(ay-cy)
        if abs(denominator) < 1e-12:
            continue
        left, right = max(0, math.ceil(min(ax,bx,cx))), min(columns-1, math.floor(max(ax,bx,cx)))
        top, bottom = max(0, math.ceil(min(ay,by,cy))), min(rows-1, math.floor(max(ay,by,cy)))
        for row in range(top, bottom+1):
            for column in range(left, right+1):
                wa = ((by-cy)*(column-cx)+(cx-bx)*(row-cy))/denominator
                wb = ((cy-ay)*(column-cx)+(ax-cx)*(row-cy))/denominator
                wc = 1-wa-wb
                if wa >= -1e-8 and wb >= -1e-8 and wc >= -1e-8:
                    elevation = wa*a[1]+wb*b[1]+wc*c[1]
                    if elevation > heights[row,column]:
                        heights[row,column] = elevation


def colorize(heights, width, depth, contour, max_height=None, visual_water=False):
    stops = np.array([0,.12,.4,.7,1])
    palette = np.array([[54,94,66],[103,133,75],[167,158,101],[180,143,105],[221,219,205]])
    rgba = np.empty((*heights.shape,4), dtype=np.uint8)
    maximum = max(float(np.nanmax(heights)) if max_height is None else max_height,1)
    for start in range(0,heights.shape[0],128):
        end = min(start+128,heights.shape[0])
        before,after = max(start-1,0),min(end+1,heights.shape[0])
        padded = np.nan_to_num(heights[before:after],nan=0)
        if padded.shape[0]>1:
            dy,dx = np.gradient(padded,depth/heights.shape[0],width/heights.shape[1])
        else:
            dy=np.zeros_like(padded)
            dx=np.gradient(padded,width/heights.shape[1],axis=1)
        section = slice(start-before,end-before)
        filled,dy,dx = padded[section],dy[section],dx[section]
        coverage = np.isfinite(heights[start:end])
        normalized = np.clip(filled/maximum,0,1)
        rgb = np.stack([np.interp(normalized,stops,palette[:,i]) for i in range(3)],axis=-1)
        shade = np.clip((1-.7*dx-.5*dy)/np.sqrt(1+dx*dx+dy*dy),.3,1.2)
        rgb *= (.65+.35*shade[:,:,None])
        rgb[filled < 0] = (25,57,74)
        if visual_water:
            rgb[~coverage] = (25,57,74)
        remainder = np.mod(filled,contour)
        contour_distance=np.minimum(remainder,contour-remainder)
        pixel_gradient=np.sqrt((dx*width/heights.shape[1])**2+(dy*depth/heights.shape[0])**2)
        opacity=np.clip(.85-contour_distance/np.maximum(pixel_gradient,.001),0,1)
        opacity[~coverage | (filled<=0) | (pixel_gradient<.01)]=0
        rgb *= (1-.45*opacity[:,:,None])
        rgba[start:end,:,:3] = np.clip(rgb,0,255).astype(np.uint8)
        rgba[start:end,:,3] = 255 if visual_water else coverage.astype(np.uint8)*255
    return Image.fromarray(rgba)


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    data = json.loads(args.scene.read_text())
    records = [r for r in data["objects"] if r["map"] == args.map]
    transforms = {identity(r): r for r in records if r["type"] == "Transform"}
    cache = {}
    def world(ref, chain=()):
        key = identity(ref)
        if key in cache:
            return cache[key]
        if key in chain:
            raise ValueError("Transform hierarchy cycle")
        record = transforms[key]
        matrix = local_matrix(record["transform"])
        if record["parent"]:
            matrix = world(record["parent"],chain+(key,)) @ matrix
        cache[key] = matrix
        return matrix
    item = next(m for m in json.loads(args.manifest.read_text())["maps"] if m["id"] == args.map)
    width, depth = item["widthMeters"], item["heightMeters"]
    if not all(math.isfinite(v) and v>0 for v in (width,depth)):
        raise ValueError("Map bounds must be positive finite meter dimensions")
    rows = max(1,round(args.resolution*depth/width))
    if rows>16384 or rows*args.resolution>16384**2:
        raise ValueError("Aspect ratio would exceed the bounded raster allocation")
    selector=ground_surface_record if args.ground_surfaces else terrain_record
    selected = [r for r in records if "mesh" in r and selector(r)]
    if not selected:
        raise ValueError("No evidence-matched base terrain meshes selected")
    heights = np.full((rows,args.resolution), -np.inf, dtype=np.float32)
    triangle_count, vertex_count = 0, 0
    bounds_min, bounds_max = np.full(3,np.inf),np.full(3,-np.inf)
    for index, record in enumerate(selected):
        filename = record["file"]
        if Path(filename).name != filename:
            raise ValueError("Unsafe geometry filename")
        vertices, faces = read_obj(args.scene.parent/filename)
        if not len(vertices) or not len(faces):
            raise ValueError(f"Base terrain mesh has no vertices or faces: {filename}")
        if not np.all(np.isfinite(vertices)):
            raise ValueError(f"Base terrain mesh contains non-finite vertices: {filename}")
        matrix = world(record["transform"])
        vertices = vertices @ matrix[:3,:3].T + matrix[:3,3]
        bounds_min = np.minimum(bounds_min,vertices.min(axis=0))
        bounds_max = np.maximum(bounds_max,vertices.max(axis=0))
        rasterize(vertices,faces,heights,width,depth)
        triangle_count += len(faces)
        vertex_count += len(vertices)
        if index%16==0:
            print(f"Rasterized {index+1}/{len(selected)} meshes",flush=True)
    heights[~np.isfinite(heights)] = np.nan
    if not np.any(np.isfinite(heights)):
        raise ValueError("No base terrain covers pixel centers within the supplied map bounds")
    np.save(args.output/"height-meters.npy",heights,allow_pickle=False)
    np.save(args.output/"coverage.npy",np.isfinite(heights),allow_pickle=False)
    colorize(heights,width,depth,args.contour,visual_water=args.visual_water).save(args.output/"topographic.png")
    report = {"schemaVersion":1,"map":args.map,"method":"highest triangle intersection at pixel centers",
        "status":"ground-surfaces" if args.ground_surfaces else "base-terrain-only","objects":len(selected),"vertices":vertex_count,"triangles":triangle_count,
        "visualWaterFill":bool(args.visual_water),
        "boundsUnityMeters":{"min":bounds_min.tolist(),"max":bounds_max.tolist()},
        "widthMeters":width,"heightMeters":depth,"rasterWidth":heights.shape[1],"rasterHeight":heights.shape[0],
        "sampleHeightMinMeters":float(np.nanmin(heights)),"sampleHeightMaxMeters":float(np.nanmax(heights)),
        "coveredPixels":int(np.isfinite(heights).sum()),"contourIntervalMeters":args.contour,
        "arrayOrder":"north-to-south rows, west-to-east columns; world X/Z pixel centers",
        "limitations":"Base terrain only; negative elevations retained where source geometry exists, missing coverage unknown; original materials excluded"}
    (args.output/"terrain.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report),flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene",required=True,type=Path)
    parser.add_argument("--manifest",required=True,type=Path)
    parser.add_argument("--map",required=True)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--resolution",default=1024,type=int)
    parser.add_argument("--contour",default=100,type=float)
    parser.add_argument("--ground-surfaces",action="store_true")
    parser.add_argument("--visual-water",action="store_true")
    args=parser.parse_args()
    if not 64<=args.resolution<=16384:
        parser.error("Resolution must be64..16384")
    if not math.isfinite(args.contour) or args.contour<=0:
        parser.error("Contour interval must be positive and finite")
    run(args)
