"""Blender exporter for a lightweight, explicitly sampled terrain derivative.

blender -b --python tools/export_height_mesh.py -- HEIGHT.npy TERRAIN.json OUTPUT --columns 512
"""
import argparse
import json
import math
import sys
from pathlib import Path
import bpy
import numpy as np


def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    source = np.load(args.height,mmap_mode="r",allow_pickle=False)
    if source.ndim!=2 or min(source.shape)<2:
        raise ValueError("Height array must be a2D raster with at least2rows and columns")
    info = json.loads(args.metadata.read_text())
    width,depth = info["widthMeters"],info["heightMeters"]
    if not all(math.isfinite(v) and v>0 for v in (width,depth)):
        raise ValueError("Map bounds must be positive finite meter dimensions")
    columns = min(args.columns,source.shape[1])
    rows = min(source.shape[0],max(2,round(columns*depth/width)))
    if rows>4096 or rows*columns>4096**2:
        raise ValueError("Aspect ratio would exceed the bounded preview allocation")
    # Keep exact source pixel positions rather than mislabel nearest samples as
    # centers of a different grid. Metadata stores each selected source index.
    xi = np.linspace(0,source.shape[1]-1,columns).round().astype(int)
    yi = np.linspace(0,source.shape[0]-1,rows).round().astype(int)
    sampled = np.asarray(source[np.ix_(yi,xi)],dtype=np.float32)
    indices = np.full(sampled.shape,-1,dtype=np.int32)
    vertices=[]
    for row in range(rows):
        z = (.5-(yi[row]+.5)/source.shape[0])*depth
        for column in range(columns):
            h = sampled[row,column]
            if np.isfinite(h):
                x = ((xi[column]+.5)/source.shape[1]-.5)*width
                indices[row,column]=len(vertices)
                vertices.append((x,-z,float(h)))
    faces=[]
    for row in range(rows-1):
        for column in range(columns-1):
            a,b,c,d = indices[row,column],indices[row,column+1],indices[row+1,column+1],indices[row+1,column]
            if a>=0 and b>=0 and c>=0:
                faces.append((a,b,c))
            if a>=0 and c>=0 and d>=0:
                faces.append((a,c,d))
    if not vertices or not faces:
        raise ValueError("Sampled coverage contains no connected terrain triangles")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    mesh=bpy.data.meshes.new("sampled-base-terrain")
    mesh.from_pydata(vertices,[],faces)
    mesh.update()
    mesh.polygons.foreach_set("use_smooth",np.ones(len(mesh.polygons),dtype=np.bool_))
    obj=bpy.data.objects.new("sampled-base-terrain",mesh)
    bpy.context.collection.objects.link(obj)
    mat=bpy.data.materials.new("neutral-terrain")
    mat.diffuse_color=(.23,.35,.21,1)
    mesh.materials.append(mat)
    bpy.ops.export_scene.gltf(filepath=str((args.output/"terrain-preview.glb").resolve()),export_format="GLB")
    sampled.astype("<f4").tofile(args.output/"height-preview.f32")
    report={"schemaVersion":1,"map":info["map"],"status":"sampled-base-terrain-derivative",
        "columns":columns,"rows":rows,"sourceColumns":source.shape[1],"sourceRows":source.shape[0],
        "sourceColumnIndices":xi.tolist(),"sourceRowIndices":yi.tolist(),
        "widthMeters":width,"heightMeters":depth,"vertices":len(vertices),"triangles":len(faces),
        "heightEncoding":"little-endian float32 meters, row-major north-to-south; NaN missing",
        "coordinateBasis":"glTF X=worldX, Y=elevation, Z=worldZ, in meters",
        "limitations":"Sampled topmost terrain surface; caves/overhangs lost; missing sampled points retained, smaller holes/features can be bridged or lost; neutral material"}
    (args.output/"terrain-preview.json").write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if not k.endswith("Indices")}),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("height",type=Path)
    parser.add_argument("metadata",type=Path)
    parser.add_argument("output",type=Path)
    parser.add_argument("--columns",default=512,type=int)
    args=parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    if not 2<=args.columns<=4096:
        parser.error("Columns must be2..4096")
    run(args)
