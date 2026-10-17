"""Physically clip a loaded static scene on CPU; never initializes a render device."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

def interpolation_gutter(interpolation):
    # Cycles samples at normalized_coordinate*size-.5: linear needs the two
    # neighboring centers; cubic needs four. One extra texel also covers the
    # inclusive upper endpoint of Closest, including exact texel boundaries.
    return {'Closest':1,'Linear':1,'Cubic':2}[interpolation]

def affine_row_product(a,b):
    if a[:2]==(0.,0.): return tuple(a[2]*v for v in b)
    if b[:2]==(0.,0.): return tuple(b[2]*v for v in a)
    return None

def recover_uv_affine(socket, uv_allowed=lambda node:True, visited=frozenset()):
    """Recover actual linear shader algebra; never approximate nonlinear nodes.

    Scalars are (u,v,constant) rows; vectors contain three such rows. Offdiagonal
    rows preserve rotated UVs and their derivatives without changing the graph.
    """
    node=socket.node
    if node.name in visited: return None
    visited=visited|{node.name}
    def input_value(inp):
        if inp.is_linked:
            source=inp.links[0].from_socket
            # Blender permits implicit scalar/vector/color conversions. Their
            # semantics are not part of this affine proof; retain the sampler.
            if source.type != inp.type: return None
            return recover_uv_affine(source,uv_allowed,visited)
        value=inp.default_value
        if inp.type=='VALUE': return (0.,0.,float(value))
        if inp.type=='VECTOR': return tuple((0.,0.,float(v)) for v in value[:3])
        return None
    if node.type in {'UVMAP','TEX_COORD'}:
        if node.type=='TEX_COORD' and socket.name!='UV': return None
        return ((1.,0.,0.),(0.,1.,0.),(0.,0.,0.)) if uv_allowed(node) else None
    if node.type=='VALUE': return (0.,0.,float(socket.default_value))
    if node.type=='SEPXYZ':
        vector=input_value(node.inputs[0])
        return vector['XYZ'.index(socket.name)] if vector is not None else None
    if node.type=='COMBXYZ':
        values=tuple(input_value(s) for s in node.inputs)
        return values if all(v is not None for v in values) else None
    if node.type=='MATH' and node.operation in {'ADD','SUBTRACT','MULTIPLY'}:
        a,b=(input_value(s) for s in node.inputs[:2])
        if a is None or b is None: return None
        row=affine_row_product(a,b) if node.operation=='MULTIPLY' else tuple(x+(y if node.operation=='ADD' else -y) for x,y in zip(a,b))
        if row is None or not all(math.isfinite(v) for v in row): return None
        if getattr(node,'use_clamp',False):
            return (0.,0.,min(1,max(0,row[2]))) if row[:2]==(0.,0.) else None
        return row
    if node.type=='MAPPING' and node.vector_type=='POINT':
        if any(node.inputs[k].is_linked for k in ('Location','Rotation','Scale')) or any(v!=0 for v in node.inputs['Rotation'].default_value): return None
        base=input_value(node.inputs['Vector'])
        if base is None: return None
        return tuple((row[0]*s,row[1]*s,row[2]*s+o) for row,s,o in zip(base,node.inputs['Scale'].default_value,node.inputs['Location'].default_value))
    if node.type=='VECT_MATH' and socket.name!='Value' and node.operation in {'ADD','SUBTRACT','MULTIPLY','MULTIPLY_ADD','SCALE'}:
        a=input_value(node.inputs[0]); b=input_value(node.inputs[3] if node.operation=='SCALE' else node.inputs[1])
        if a is None or b is None: return None
        if node.operation=='SCALE': b=(b,)*3
        if node.operation in {'MULTIPLY','MULTIPLY_ADD','SCALE'}:
            rows=tuple(affine_row_product(x,y) for x,y in zip(a,b))
            if any(row is None for row in rows): return None
            if node.operation=='MULTIPLY_ADD':
                extra=input_value(node.inputs[2])
                if extra is None: return None
                rows=tuple(tuple(x+y for x,y in zip(row,add)) for row,add in zip(rows,extra))
        else: rows=tuple(tuple(x+(y if node.operation=='ADD' else -y) for x,y in zip(ra,rb)) for ra,rb in zip(a,b))
        return rows if all(math.isfinite(v) for row in rows for v in row) else None
    return None

def transformed_uvs(transform, uvs):
    if len(transform)==4:
        sx,sy,ox,oy=transform; transform=(sx,0.,ox,0.,sy,oy)
    a,b,c,d,e,f=transform
    return ([a*u+b*v+c for u,v in uvs],[d*u+e*v+f for u,v in uvs])

def periodic_crop_axis(low, high, size, gutter=4):
    """Source texel indices and unwrapped origin for an affine REPEAT axis.

    Copy across the seam rather than applying fract to interpolated UVs. A
    full-period axis retains its original period, coordinates and filtering.
    The gutter includes both sides of every supported interpolation kernel.
    """
    if not all(math.isfinite(v) for v in (low, high)) or low > high:
        raise ValueError('Invalid finite sampler domain')
    if not isinstance(size, int) or size < 1 or gutter < 0:
        raise ValueError('Invalid source size/gutter')
    start=math.floor(low*size)-gutter
    stop=math.ceil(high*size)+gutter
    stop=max(start+1,stop)
    if stop-start >= size:
        return list(range(size)), 0, True
    return [i % size for i in range(start,stop)], start, False

def exact_zero_channels(pixels):
    """Prove channel zero over every retained texel; no epsilon threshold."""
    return tuple(all(math.isfinite(float(pixel[c])) and pixel[c] == 0
                     for pixel in pixels) for c in range(4)) if len(pixels) else (False,)*4

def exact_endpoint_channels(pixels, color_space, extension='REPEAT'):
    """Only invariant endpoint colors; opaque sRGB avoids alpha conversion."""
    # CLIP introduces zero outside the image even for a uniform-one image.
    # A cropped texel proof alone does not establish that its UVs stay inside.
    if extension not in {'REPEAT','EXTEND'} or not len(pixels) or color_space not in {'Non-Color','sRGB'}:
        return (None,)*4
    opaque=all(pixel[3]==1 for pixel in pixels)
    if color_space=='sRGB' and not opaque:
        return (None,)*4
    return tuple(next((value for value in (0.0,1.0)
                       if (c==3 or value==0 or opaque)
                       if all(math.isfinite(float(pixel[c])) and pixel[c]==value
                              for pixel in pixels)),None) for c in range(4))

def prune_zero_weight_branches(tree, proved_outputs):
    """Specialize only certified endpoint channels and literal endpoint weights.

    Keep scalar computations, disconnect a zero-weight SCALE vector and bypass
    an unclamped plain MIX at zero/one. Then remove unreachable nodes. The proof
    caller must include the complete retained domain and filtering support.
    """
    records=[]
    for proof in proved_outputs:
        output,value=proof if isinstance(proof,tuple) else (proof,0.0)
        if value not in (0.0,1.0): raise ValueError('Only exact endpoint proofs allowed')
        for link in list(output.links):
            target=link.to_socket
            if target.type!='VALUE': continue
            tree.links.remove(link); target.default_value=value
            records.append({'proof':'exact-endpoint-image-channel','value':value,'node':target.node.name,'socket':target.name})
    def scalar(socket, visited=None):
        if not socket.is_linked: return socket.default_value if socket.type=='VALUE' else None
        output=socket.links[0].from_socket; node=output.node
        visited=set() if visited is None else visited
        if node.name in visited: return None
        visited=visited|{node.name}
        if node.type=='VALUE': return output.default_value
        if node.type=='MATH' and node.operation=='MULTIPLY':
            values=[scalar(s,visited) for s in node.inputs[:2]]
            if 0 in values: return 0.0
            if all(v is not None and math.isfinite(v) for v in values):
                value=values[0]*values[1]
                if not math.isfinite(value): return None
                return min(1,max(0,value)) if getattr(node,'use_clamp',False) else value
        return None
    for node in list(tree.nodes):
        if node.type=='VECT_MATH' and node.operation=='SCALE' and scalar(node.inputs[3])==0:
            vector=node.inputs[0]
            if vector.is_linked:
                tree.links.remove(vector.links[0]); vector.default_value=(0,0,0)
                records.append({'proof':'exact-zero-scale','node':node.name})
        if node.type=='MIX_RGB' and node.blend_type=='MIX' and not node.use_clamp and not node.use_alpha and scalar(node.inputs[0]) in (0,1):
            endpoint=scalar(node.inputs[0])
            first=node.inputs[1+int(endpoint)]
            source=first.links[0].from_socket if first.is_linked else None
            if source is None and any(link.to_socket.type!='RGBA' for link in node.outputs[0].links): continue
            for link in list(node.outputs[0].links):
                target=link.to_socket
                tree.links.remove(link)
                if source: tree.links.new(source,target)
                elif target.type=='RGBA': target.default_value=first.default_value
                else: raise ValueError('Cannot exactly replace default mix color in converted socket')
            records.append({'proof':'exact-endpoint-mix','value':endpoint,'node':node.name})
    reachable=set()
    def visit(node):
        if node.name in reachable: return
        reachable.add(node.name)
        for socket in node.inputs:
            for link in socket.links: visit(link.from_node)
    for node in tree.nodes:
        if node.type=='OUTPUT_MATERIAL': visit(node)
    removed=[node.name for node in tree.nodes if node.name not in reachable]
    for name in removed: tree.nodes.remove(tree.nodes[name])
    return {'specializations':records,'removedNodes':removed}

def caster_offsets(ray, height_range, sun_angle=0.0):
    """Conservative XY caster offsets opposite incoming rays, including sun cone.

    A caster at height H shadows a receiver after H/(-ray.z) travel. Thus its
    location is receiver - H*ray.xy/(-ray.z). Every unit direction in the sun's
    half-angle cone differs by at most 2*sin(half_angle/2) per component. Bound
    the quotient perturbation with that distance and the smallest denominator.
    Including zero covers every height between receiver and maximum caster.
    """
    if height_range < 0 or not math.isfinite(height_range) or not 0<=sun_angle<math.pi:
        raise ValueError('Invalid height range or sun angle')
    length=math.sqrt(sum(v*v for v in ray))
    if not math.isfinite(length) or length==0: raise ValueError('Invalid sun ray')
    x,y,z=(v/length for v in ray)
    d=-z; epsilon=2*math.sin(sun_angle/4)
    if d<=max(.05,epsilon): raise ValueError('Sun cone does not bound downward shadows')
    ranges=[]
    for component in (x,y):
        center=-height_range*component/d
        error=height_range*epsilon*(d+abs(component))/(d*(d-epsilon))
        ranges.append((min(0.,center-error),max(0.,center+error)))
    return ranges[0][0],ranges[1][0],ranges[0][1],ranges[1][1]

def projected_shadow_bounds(triangle, receiver_min_z, ray, sun_angle=0.0):
    """Conservative receiver XY box for every point on a caster triangle.

    All receivers are at or above receiver_min_z. Varying the caster height
    from zero to its maximum separation enlarges the projection, including
    sloped triangles and every ray in the sun cone. Below-receiver triangles
    cannot block a downward direct sun ray.
    """
    if len(triangle)!=3 or not math.isfinite(receiver_min_z) or any(
            len(p)<3 or not all(math.isfinite(v) for v in p[:3]) for p in triangle):
        raise ValueError('Invalid triangle/receiver height')
    height=max(p[2] for p in triangle)-receiver_min_z
    if height<0: return None
    left,bottom,right,top=caster_offsets(ray,height,sun_angle)
    return (min(p[0] for p in triangle)-right,
            min(p[1] for p in triangle)-top,
            max(p[0] for p in triangle)-left,
            max(p[1] for p in triangle)-bottom)

def boxes_intersect(a,b):
    return a[0]<=b[2] and a[2]>=b[0] and a[1]<=b[3] and a[3]>=b[1]

def direct_caster_region(triangle, receiver, receiver_min_z, ray, sun_angle=0.0):
    """Clip only the possible caster portion, never retain a whole giant face.

    A point casting onto receiver XY must lie within receiver + the conservative
    backwards sun-ray offsets. This is valid without visibility/occlusion
    assumptions, including a map-wide plane at the minimum receiver height.
    """
    projected=projected_shadow_bounds(triangle,receiver_min_z,ray,sun_angle)
    if projected is None or not boxes_intersect(projected,receiver): return []
    offsets=caster_offsets(ray,max(p[2] for p in triangle)-receiver_min_z,sun_angle)
    bounds=(receiver[0]+offsets[0],receiver[1]+offsets[1],
            receiver[2]+offsets[2],receiver[3]+offsets[3])
    return clip_polygon(triangle,bounds)

def retained_material_slots(face_indices, slot_count):
    """Only surviving face assignments keep materials resident after clipping."""
    used=sorted(set(face_indices))
    if slot_count==0: return [], {index:index for index in used}
    if any(index<0 or index>=slot_count for index in used):
        raise ValueError('Source face material index is outside its material slots')
    return used, {index:new_index for new_index,index in enumerate(used)}

def clip_polygon(poly, bounds):
    """Sutherland-Hodgman, interpolating all trailing per-corner attributes."""
    for axis, limit, sign in ((0,bounds[0],1),(0,bounds[2],-1),(1,bounds[1],1),(1,bounds[3],-1)):
        result = []
        if not poly:
            break
        previous = poly[-1]
        dp = sign*(previous[axis]-limit)
        for current in poly:
            dc = sign*(current[axis]-limit)
            if (dp >= 0) != (dc >= 0):
                t = dp/(dp-dc)
                result.append(tuple(a+t*(b-a) for a,b in zip(previous,current)))
            if dc >= 0:
                result.append(current)
            previous, dp = current, dc
        poly = result
    return poly

def main(argv):
    import bpy
    import numpy as np
    from mathutils import Vector
    from render_safety import configure_scene, lower_process_priority, render_slot
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--x',type=float,default=12800)
    p.add_argument('--z',type=float,default=7680)
    p.add_argument('--edge',type=int,default=512)
    p.add_argument('--mpp',type=float,default=1.64)
    p.add_argument('--directional-halo',action='store_true',help='Keep only conservative directional sun-caster rectangle, including sun angular cone')
    p.add_argument('--local-direct-halo',action='store_true',help='Use actual receiver minimum and individual triangle direct-sun projections; indirect lighting context remains unproved')
    p.add_argument('--texture-edge',type=int,default=0,help='Optional lossy planning variant; 0 preserves exact source texels')
    p.add_argument('--preserve-material',action='append',default=['Terrain1'],help='Keep exact physically cropped texels for this explicitly selected material')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--max-output-bytes',type=int,default=0,help='Optional private candidate disk ceiling; refuse large packed payloads before saving')
    args=p.parse_args(argv)
    if args.max_output_bytes<0: p.error('Output byte ceiling must be nonnegative')
    if args.texture_edge and not 32<=args.texture_edge<=1024:
        p.error('Texture edge must be 0 or 32..1024')
    if not all(math.isfinite(v) for v in (args.x,args.z,args.mpp)) or args.mpp<=0 or not 1<=args.edge<=1028:
        p.error('Invalid finite footprint/edge')
    output=args.output.resolve()
    if output.exists():
        p.error('Output must be fresh')
    source=Path(bpy.data.filepath).resolve()
    if not source.is_file() or output==source.parent:
        p.error('Load source first and use a separate output directory')
    lower_process_priority()
    with render_slot():
        scene=bpy.context.scene
        configure_scene(scene,threads=2,samples=16,render_edge=args.edge)
        from source_light_mesh import apply as canonicalize_lights, material_ids as light_material_ids
        light_targets=light_material_ids(bpy.data.materials)
        light_face_proof=canonicalize_lights(scene,light_targets) if light_targets else None
        if light_face_proof is not None:
            light_face_proof['stage']='beforePhysicalClip'
        original_stat=source.stat()
        with source.open('rb') as handle:
            source_hash=hashlib.file_digest(handle,'sha256').hexdigest()
        objects=list(scene.objects)
        geometry=[o for o in objects if o.type=='MESH' and not o.hide_render]
        unsupported=[o.name for o in objects if o.type not in {'MESH','LIGHT','CAMERA','EMPTY'}]
        if unsupported or any(o.modifiers or o.instance_type!='NONE' for o in geometry):
            raise ValueError('Requires static plain meshes without modifiers/instances: '+repr(unsupported))
        corners=[o.matrix_world@Vector(v) for o in geometry for v in o.bound_box]
        low=min(v.z for v in corners); top=max(v.z for v in corners)
        halo=0.0
        suns=[]
        directional=[0.,0.,0.,0.]
        for o in objects:
            if o.type=='LIGHT' and o.data.type=='SUN':
                ray=o.matrix_world.to_quaternion()@Vector((0,0,-1))
                if ray.z>=-0.05:
                    raise ValueError('Sun direction does not bound downward shadows')
                reach=(top-low)*math.hypot(ray.x,ray.y)/abs(ray.z)
                halo=max(halo,reach)
                offsets=caster_offsets(ray,top-low,o.data.angle)
                directional=[min(directional[0],offsets[0]),min(directional[1],offsets[1]),max(directional[2],offsets[2]),max(directional[3],offsets[3])]
                suns.append({'name':o.name,'ray':list(ray),'reachMeters':reach,'angleRadians':o.data.angle,'casterOffsetsXY':offsets})
        half=args.edge*args.mpp/2
        bounds=(args.x-half-halo,-args.z-half-halo,args.x+half+halo,-args.z+half+halo)
        if args.directional_halo:
            bounds=(args.x-half+directional[0],-args.z-half+directional[1],args.x+half+directional[2],-args.z+half+directional[3])
        for o in geometry: o.data.calc_loop_triangles()
        local_halo=None
        if args.local_direct_halo:
            receiver=(args.x-half,-args.z-half,args.x+half,-args.z+half)
            receiver_min=math.inf; receiver_triangles=0
            # Two streaming passes avoid retaining another full source mesh.
            for o in geometry:
                object_box=[o.matrix_world@Vector(v) for v in o.bound_box]
                object_xy=(min(v.x for v in object_box),min(v.y for v in object_box),max(v.x for v in object_box),max(v.y for v in object_box))
                if not boxes_intersect(object_xy,receiver): continue
                points=[o.matrix_world@v.co for v in o.data.vertices]
                for tri in o.data.loop_triangles:
                    clipped=clip_polygon([tuple(points[i]) for i in tri.vertices],receiver)
                    if len(clipped)>=3:
                        receiver_min=min(receiver_min,min(p[2] for p in clipped))
                        receiver_triangles+=1
            if not math.isfinite(receiver_min):
                raise ValueError('Local direct halo requires actual geometry in receiver footprint')
            # The global angular halo still supplies an outer proof bound.
            global_bounds=(receiver[0]+directional[0],receiver[1]+directional[1],
                           receiver[2]+directional[2],receiver[3]+directional[3])
            kept=list(receiver); caster_triangles=0
            for o in geometry:
                object_box=[o.matrix_world@Vector(v) for v in o.bound_box]
                object_xy=(min(v.x for v in object_box),min(v.y for v in object_box),max(v.x for v in object_box),max(v.y for v in object_box))
                if not boxes_intersect(object_xy,global_bounds): continue
                points=[o.matrix_world@v.co for v in o.data.vertices]
                for tri in o.data.loop_triangles:
                    triangle=[tuple(points[i]) for i in tri.vertices]
                    box=(min(p[0] for p in triangle),min(p[1] for p in triangle),
                         max(p[0] for p in triangle),max(p[1] for p in triangle))
                    if not boxes_intersect(box,global_bounds): continue
                    caster_points=[]
                    for sun in suns:
                        region=direct_caster_region(triangle,receiver,receiver_min,sun['ray'],sun['angleRadians'])
                        caster_points.extend(clip_polygon(region,global_bounds))
                    if len(caster_points)<3: continue
                    caster_triangles+=1
                    kept=[min(kept[0],min(p[0] for p in caster_points)),
                          min(kept[1],min(p[1] for p in caster_points)),
                          max(kept[2],max(p[0] for p in caster_points)),
                          max(kept[3],max(p[1] for p in caster_points))]
            bounds=tuple(kept)
            local_halo={'receiverBoundsXY':receiver,'receiverMinimumZ':receiver_min,
                        'receiverSourceTriangles':receiver_triangles,'directCasterSourceTriangles':caster_triangles,
                        'globalAngularBoundsXY':global_bounds,'retainedBoundsXY':bounds,
                        'directSunCoverageConservative':True,'casterBoundsUseClippedPolygons':True,
                        'indirectLightingContextVerified':False}
        before={'objects':len(geometry),'triangles':sum(len(o.data.loop_triangles) for o in geometry)}
        material_uv={}; material_uv_names={}; material_uv_active={}
        for o in objects:
            if o.type!='MESH':
                if o.type not in {'LIGHT','CAMERA'}:
                    bpy.data.objects.remove(o,do_unlink=True)
                continue
            if o.hide_render:
                bpy.data.objects.remove(o,do_unlink=True); continue
            mesh=o.data; mesh.calc_loop_triangles()
            points=[o.matrix_world@v.co for v in mesh.vertices]
            if max(v.x for v in points)<bounds[0] or min(v.x for v in points)>bounds[2] or max(v.y for v in points)<bounds[1] or min(v.y for v in points)>bounds[3]:
                bpy.data.objects.remove(o,do_unlink=True); continue
            normal_matrix=o.matrix_world.to_3x3().inverted().transposed()
            normals=mesh.corner_normals
            uv_layers=list(mesh.uv_layers)
            verts=[]; faces=[]; attrs=[]; mats=[]; smooth=[]
            for triangle in mesh.loop_triangles:
                polygon=mesh.polygons[triangle.polygon_index]
                poly=[]
                for li in triangle.loops:
                    pos=points[mesh.loops[li].vertex_index]
                    normal=(normal_matrix@normals[li].vector).normalized()
                    poly.append(tuple(pos)+tuple(normal)+tuple(c for layer in uv_layers for c in layer.data[li].uv))
                clipped=clip_polygon(poly,bounds)
                if len(clipped)<3: continue
                # Separate corners retain UV seams and original split normals.
                for i in range(1,len(clipped)-1):
                    face=(clipped[0],clipped[i],clipped[i+1])
                    start=len(verts); verts.extend(v[:3] for v in face)
                    faces.append((start,start+1,start+2)); attrs.extend(face)
                    mats.append(polygon.material_index); smooth.append(polygon.use_smooth)
            if not faces:
                bpy.data.objects.remove(o,do_unlink=True); continue
            new=bpy.data.meshes.new(mesh.name+' cropped')
            new.from_pydata(verts,[],faces)
            used_slots,material_remap=retained_material_slots(mats,len(mesh.materials))
            for index in used_slots: new.materials.append(mesh.materials[index])
            for poly,mi,sm in zip(new.polygons,mats,smooth): poly.material_index=material_remap[mi]; poly.use_smooth=sm
            for ui,layer in enumerate(uv_layers):
                uv=new.uv_layers.new(name=layer.name)
                uv.data.foreach_set('uv',[c for a in attrs for c in a[6+ui*2:8+ui*2]])
            if uv_layers:
                new.uv_layers.active_index=mesh.uv_layers.active_index
                for ui,layer in enumerate(uv_layers):
                    if layer.active_render: new.uv_layers[ui].active_render=True
            new.normals_split_custom_set([Vector(a[3:6]).normalized() for a in attrs])
            o.data=new; o.matrix_world.identity()
            for poly in new.polygons:
                mat=new.materials[poly.material_index] if new.materials else None
                if mat and uv_layers:
                    material_uv.setdefault(mat.name,[]).extend(tuple(new.uv_layers[0].data[li].uv) for li in poly.loop_indices)
                    material_uv_names.setdefault(mat.name,set()).add(uv_layers[0].name)
                    # Unconnected/TexCoord UV uses the render-active layer;
                    # do not bound that sampler using a different layer.
                    material_uv_active.setdefault(mat.name,set()).add(next((u.name for u in uv_layers if u.active_render),uv_layers[0].name))
        print('Geometry clipped',flush=True)
        def affine(socket):
            def allowed(node):
                wanted=({node.uv_map} if node.type=='UVMAP' and node.uv_map else material_uv_active[name])
                return wanted==material_uv_names[name]
            rows=recover_uv_affine(socket,allowed)
            if rows is None or len(rows)!=3 or not isinstance(rows[0],tuple): return None
            x,y,_=rows
            return (x[0],y[1],x[2],y[2]) if x[1]==0 and y[0]==0 else x+y
        crops=[]; retained=[]
        output.mkdir(parents=True)
        def crop_material(name,uvs,second_pass=False):
            mat=bpy.data.materials[name]
            if not mat.use_nodes: return
            for node in list(mat.node_tree.nodes):
                if node.type!='TEX_IMAGE' or not node.image: continue
                if second_pass and any(r['material']==name and r['node']==node.name for r in crops): continue
                image=node.image; w,h=image.size
                inp=node.inputs['Vector']
                # Blender Image Texture's unconnected vector samples active UV.
                transform=affine(inp.links[0].from_socket) if inp.is_linked else ((1.,1.,0.,0.) if material_uv_active[name]==material_uv_names[name] else None)
                if transform is None:
                    retained.append({'material':name,'node':node.name,'image':image.name,'upstreamType':inp.links[0].from_node.type if inp.is_linked else None,'reason':'non-affine or unverified UV-layer graph preserved'}); continue
                us,vs=transformed_uvs(transform,uvs)
                if node.projection!='FLAT' or node.interpolation not in {'Closest','Linear','Cubic'}:
                    retained.append({'material':name,'node':node.name,'image':image.name,'reason':'projection/interpolation crop equivalence unproved'}); continue
                gutter=interpolation_gutter(node.interpolation)
                if node.extension=='REPEAT':
                    xs,left,full_x=periodic_crop_axis(min(us),max(us),w,gutter)
                    ys,bottom,full_y=periodic_crop_axis(min(vs),max(vs),h,gutter)
                    right=left+len(xs); upper=bottom+len(ys)
                else:
                    # Preserve existing nonperiodic crop behavior.
                    left=max(0,min(w-1,math.floor(min(us)*w)-gutter)); right=max(left+1,min(w,math.ceil(max(us)*w)+gutter))
                    bottom=max(0,min(h-1,math.floor(min(vs)*h)-gutter)); upper=max(bottom+1,min(h,math.ceil(max(vs)*h)+gutter))
                    xs=list(range(left,right)); ys=list(range(bottom,upper))
                if (right-left)*(upper-bottom)>=w*h*.95: continue
                pixels=np.empty(w*h*4,dtype=np.float32); image.pixels.foreach_get(pixels)
                crop=pixels.reshape(h,w,4)[np.ix_(ys,xs)].copy()
                result=bpy.data.images.new(image.name+' physical crop',width=right-left,height=upper-bottom,alpha=True,float_buffer=True)
                result.colorspace_settings.name=image.colorspace_settings.name
                result.pixels.foreach_set(crop.ravel()); result.pack()
                adjustment=mat.node_tree.nodes.new('ShaderNodeVectorMath'); adjustment.operation='MULTIPLY_ADD'
                adjustment.label='Physical image crop; original UV unchanged'
                adjustment.inputs[1].default_value=(w/(right-left),h/(upper-bottom),1)
                adjustment.inputs[2].default_value=(-left/(right-left),-bottom/(upper-bottom),0)
                if inp.is_linked:
                    coordinates=inp.links[0].from_socket
                else:
                    coordinates=mat.node_tree.nodes.new('ShaderNodeTexCoord').outputs['UV']
                mat.node_tree.links.new(coordinates,adjustment.inputs[0])
                mat.node_tree.links.new(adjustment.outputs[0],inp); node.image=result
                crops.append({'source':image.name,'sourceSize':[w,h],'size':list(result.size),'pixelBoxBottomOrigin':[left,bottom,right,upper],'material':name,'node':node.name,'coordinateTransform':transform,'periodicSourceIndices':node.extension=='REPEAT','filterGutterTexels':gutter,'interpolation':node.interpolation,'sourceTexelResolutionPreserved':True})
        for name,uvs in material_uv.items(): crop_material(name,uvs)
        zero_proofs=[]; endpoint_proofs=[]; shader_pruning=[]
        for name in material_uv:
            mat=bpy.data.materials[name]
            if not mat.use_nodes: continue
            proved=[]
            for record in crops:
                if record['material']!=name: continue
                node=mat.node_tree.nodes.get(record['node'])
                if not node: continue
                image=node.image; w,h=image.size
                pixels=np.empty(w*h*4,dtype=np.float32); image.pixels.foreach_get(pixels)
                endpoints=exact_endpoint_channels(pixels.reshape(-1,4),image.colorspace_settings.name,node.extension)
                for link in node.outputs['Color'].links:
                    if link.to_node.type!='SEPXYZ': continue
                    for channel,output_socket in enumerate(link.to_node.outputs):
                        if endpoints[channel] is not None:
                            value=endpoints[channel]
                            proved.append((output_socket,value))
                            record={'material':name,'image':image.name,'channel':channel,'value':value,'retainedTexels':w*h,'colorSpace':image.colorspace_settings.name,'proof':'every cropped source texel including filtering gutters is the same exact color endpoint; sRGB requires opaque alpha'}
                            endpoint_proofs.append(record)
                            if value==0: zero_proofs.append(record)
            result=prune_zero_weight_branches(mat.node_tree,proved)
            if result['specializations'] or result['removedNodes']:
                shader_pruning.append({'material':name,**result})
        # Endpoint specialization can turn formerly nonlinear samplers into
        # exact affine maps (e.g. the source field-angle zero branch).
        retained=[]
        for name,uvs in material_uv.items(): crop_material(name,uvs,True)
        bpy.data.orphans_purge(do_local_ids=True,do_linked_ids=True,do_recursive=True)
        reductions=[]
        if args.texture_edge:
            protected={n.image.name for m in bpy.data.materials if m.name in args.preserve_material and m.use_nodes for n in m.node_tree.nodes if n.type=='TEX_IMAGE' and n.image and ' physical crop' in n.image.name}
            for image in bpy.data.images:
                w,h=image.size
                # Explicitly selected terrain crops retain exact texels. Other
                # samples use an openly lossy, comparison-only cap.
                if max(w,h)>args.texture_edge and image.name not in protected:
                    factor=args.texture_edge/max(w,h)
                    new=(max(1,round(w*factor)),max(1,round(h*factor)))
                    image.scale(*new); image.pack()
                    reductions.append({'name':image.name,'before':[w,h],'after':list(new),'filter':'Blender image.scale; decoded image channels','fidelityVerified':False})
        bpy.context.preferences.filepaths.save_version=0
        scene.render.resolution_x=scene.render.resolution_y=args.edge
        scene.render.resolution_percentage=100
        if scene.camera:
            scene.camera.location=(args.x,-args.z,top+8000)
            scene.camera.rotation_euler=(0,0,0)
            scene.camera.data.type='ORTHO'; scene.camera.data.ortho_scale=args.edge*args.mpp
        images=[{'name':im.name,'width':im.size[0],'height':im.size[1],'channels':im.channels,'float':im.is_float,'colorSpace':im.colorspace_settings.name,'floatRgbaMipBytes':math.ceil(im.size[0]*im.size[1]*16*4/3)} for im in bpy.data.images]
        meshes=[{'name':m.name,'vertices':len(m.vertices),'loops':len(m.loops),'triangles':len(m.polygons),'uvLayers':[u.name for u in m.uv_layers]} for m in bpy.data.meshes]
        destination=output/'candidate.blend'
        if args.max_output_bytes:
            packed_bytes=sum(im.packed_file.size for im in bpy.data.images if im.packed_file)
            planning_bytes=packed_bytes+sum(m['vertices']*256+m['loops']*128 for m in meshes)+2*1024*1024
            if planning_bytes>args.max_output_bytes:
                refusal={'source':str(source),'sourceSha256':source_hash,'edge':args.edge,
                         'centerX':args.x,'centerZ':args.z,'metersPerPixel':args.mpp,
                         'cropBoundsBlenderXY':bounds,'localDirectHalo':local_halo,
                         'meshes':meshes,'images':images,'imageCrops':crops,'imageReductions':reductions,
                         'retainedImageSamples':retained,'exactEndpointChannelProofs':endpoint_proofs,
                         'shaderBranchPruning':shader_pruning,'gpuInitialized':False,'rendered':False,
                         'candidateSaved':False,'diskPayloadPlanningBytes':planning_bytes,
                         'packedImageBytes':packed_bytes,'outputByteCeiling':args.max_output_bytes,
                         'reason':'Packed payload plus conservative geometry/metadata reserve exceeds disk ceiling',
                         'materials':[{'name':m.name,'nodes':[{'name':n.name,'type':n.type,'image':n.image.name if n.type=='TEX_IMAGE' and n.image else None} for n in m.node_tree.nodes]} for m in bpy.data.materials if m.use_nodes]}
                (output/'preparation-refusal.json').write_text(json.dumps(refusal,indent=2),encoding='utf-8')
                raise ValueError(f'Packed payload plus conservative geometry/metadata disk reserve {planning_bytes} exceeds candidate output ceiling {args.max_output_bytes}')
        bpy.ops.wm.save_as_mainfile(filepath=str(destination),compress=True)
        if args.max_output_bytes and destination.stat().st_size>args.max_output_bytes:
            # Only this newly generated candidate, never a source/prior scene.
            destination.unlink()
            raise ValueError('Saved candidate exceeded output ceiling; fresh generated scene removed')
        evidence={'source':str(source),'sourceSha256':source_hash,'sourceBytes':original_stat.st_size,'outputBytes':destination.stat().st_size,'centerX':args.x,'centerZ':args.z,'edge':args.edge,'metersPerPixel':args.mpp,'footprintMeters':args.edge*args.mpp,'cropBoundsBlenderXY':bounds,'heightRange':[low,top],'sunShadowHaloMeters':halo,'suns':suns,'before':before,'meshes':meshes,'images':images,'imageCrops':crops,'retainedImageSamples':retained,'objects':[{'name':o.name,'type':o.type} for o in scene.objects],'decodedRgbaMipBytes':sum(im['floatRgbaMipBytes'] for im in images),'gpuInitialized':False,'rendered':False,'devicePeakVerified':False,'fidelityGaps':['Indirect illumination and sun angular penumbra beyond the center-ray shadow halo are not bounded; matched CPU comparison pending.','Affine repeating crops preserve source texel resolution with periodic filtering gutters; nonlinear graphs and full-period axes retain source images. Matched-render filtering/color fidelity remains unverified.','GPU allocation and time bounds are not established.'],'threads':2,'device':'CPU'}
        evidence['imageReductions']=reductions
        evidence['exactZeroChannelProofs']=zero_proofs
        evidence['exactEndpointChannelProofs']=endpoint_proofs
        evidence['shaderBranchPruning']=shader_pruning
        evidence['haloMode']='directional-sun-angular-cone' if args.directional_halo else 'symmetric-center-ray'
        if local_halo:
            evidence['haloMode']='local-receiver-per-triangle-direct-sun-angular-cone'
            evidence['localDirectHalo']=local_halo
        evidence['directionalCasterOffsetsXY']=directional
        if args.directional_halo:
            evidence['fidelityGaps'][0]='Directional bounds cover direct sun rays including angular penumbra using full source height range; indirect illumination beyond finite crop remains unbounded, and matched CPU comparison is pending.'
        if local_halo:
            evidence['fidelityGaps'][0]='Local receiver height and per-triangle angular-cone projections conservatively retain direct sun casters; two-bounce indirect lighting from removed context is unproved and requires matched CPU comparison against the full-halo reference.'
        with destination.open('rb') as handle:
            evidence['outputSha256']=hashlib.file_digest(handle,'sha256').hexdigest()
        evidence['materials']=[{'name':m.name,'nodes':[{'name':n.name,'type':n.type,'operation':getattr(n,'operation',None),'image':n.image.name if n.type=='TEX_IMAGE' and n.image else None} for n in m.node_tree.nodes],'links':[{'fromNode':l.from_node.name,'fromSocket':l.from_socket.name,'toNode':l.to_node.name,'toSocket':l.to_socket.name} for l in m.node_tree.links]} for m in bpy.data.materials if m.use_nodes]
        evidence['passes']=[{'name':l.name,'enabled':[p.identifier for p in l.bl_rna.properties if p.identifier.startswith('use_pass_') and getattr(l,p.identifier)]} for l in scene.view_layers]
        if reductions:
            evidence['fidelityGaps'][1]='Explicit lossy texture-cap variant: minification, wrap filtering, alpha/normal and color fidelity require matched CPU comparison; not accepted imagery.'
        if light_face_proof is not None:
            evidence['sourceLightFaceCanonicalization']=light_face_proof
        (output/'manifest.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        if source.stat().st_size!=original_stat.st_size or source.stat().st_mtime_ns!=original_stat.st_mtime_ns:
            raise RuntimeError('Source changed during preprocessing')
        print(json.dumps({'output':str(destination),'meshCount':len(meshes),'triangles':sum(m['triangles'] for m in meshes),'imageCount':len(images),'imageBytes':evidence['decodedRgbaMipBytes'],'crops':len(crops),'halo':halo}),flush=True)

if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    main(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:])
