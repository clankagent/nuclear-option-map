"""Physically clip a loaded static scene on CPU; never initializes a render device."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

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
    p.add_argument('--texture-edge',type=int,default=0,help='Optional lossy planning variant; 0 preserves exact source texels')
    p.add_argument('--preserve-material',action='append',default=['Terrain1'],help='Keep exact physically cropped texels for this explicitly selected material')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(argv)
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
        before={'objects':len(geometry),'triangles':sum(len(o.data.loop_triangles) for o in geometry)}
        material_uv={}
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
            for mat in mesh.materials: new.materials.append(mat)
            for poly,mi,sm in zip(new.polygons,mats,smooth): poly.material_index=mi; poly.use_smooth=sm
            for ui,layer in enumerate(uv_layers):
                uv=new.uv_layers.new(name=layer.name)
                uv.data.foreach_set('uv',[c for a in attrs for c in a[6+ui*2:8+ui*2]])
            new.normals_split_custom_set([Vector(a[3:6]).normalized() for a in attrs])
            o.data=new; o.matrix_world.identity()
            for poly in new.polygons:
                mat=new.materials[poly.material_index] if new.materials else None
                if mat and uv_layers:
                    material_uv.setdefault(mat.name,[]).extend(tuple(new.uv_layers[0].data[li].uv) for li in poly.loop_indices)
        print('Geometry clipped',flush=True)
        def constant(socket):
            if not socket.is_linked: return socket.default_value
            node=socket.links[0].from_node
            if node.type=='COMBXYZ' and not any(s.is_linked for s in node.inputs): return tuple(s.default_value for s in node.inputs)
            return None
        def affine(socket):
            node=socket.node
            if node.type=='UVMAP' or (node.type=='TEX_COORD' and socket.name=='UV'): return (1.,1.,0.,0.)
            if node.type=='MAPPING' and node.vector_type=='POINT' and node.inputs['Vector'].is_linked:
                if any(node.inputs[key].is_linked for key in ('Location','Rotation','Scale')) or any(abs(v)>1e-12 for v in node.inputs['Rotation'].default_value): return None
                base=affine(node.inputs['Vector'].links[0].from_socket)
                if base is None: return None
                sx,sy,ox,oy=base; scale=node.inputs['Scale'].default_value; offset=node.inputs['Location'].default_value
                return sx*scale[0],sy*scale[1],ox*scale[0]+offset[0],oy*scale[1]+offset[1]
            if node.type=='VECT_MATH' and node.operation in {'MULTIPLY_ADD','MULTIPLY','ADD','SCALE'}:
                if not node.inputs[0].is_linked: return None
                base=affine(node.inputs[0].links[0].from_socket)
                if base is None: return None
                sx,sy,ox,oy=base
                if node.operation=='SCALE':
                    k=constant(node.inputs[3])
                    return (sx*k,sy*k,ox*k,oy*k) if k is not None else None
                v=constant(node.inputs[1])
                if v is None:return None
                if node.operation=='ADD': return sx,sy,ox+v[0],oy+v[1]
                extra=constant(node.inputs[2]) if node.operation=='MULTIPLY_ADD' else (0,0,0)
                if extra is None:return None
                return sx*v[0],sy*v[1],ox*v[0]+extra[0],oy*v[1]+extra[1]
            return None
        crops=[]; retained=[]
        output.mkdir(parents=True)
        for name,uvs in material_uv.items():
            mat=bpy.data.materials[name]
            if not mat.use_nodes: continue
            for node in list(mat.node_tree.nodes):
                if node.type!='TEX_IMAGE' or not node.image: continue
                image=node.image; w,h=image.size
                inp=node.inputs['Vector']
                # Blender Image Texture's unconnected vector samples active UV.
                transform=affine(inp.links[0].from_socket) if inp.is_linked else (1.,1.,0.,0.)
                if transform is None:
                    retained.append({'material':name,'node':node.name,'image':image.name,'upstreamType':inp.links[0].from_node.type if inp.is_linked else None,'reason':'non-affine/repeating detail graph preserved'}); continue
                sx,sy,ox,oy=transform
                us=[u*sx+ox for u,v in uvs]; vs=[v*sy+oy for u,v in uvs]
                if node.extension=='REPEAT' and (min(us)<0 or max(us)>1 or min(vs)<0 or max(vs)>1):
                    retained.append({'image':image.name,'reason':'domain crosses repeat boundary'}); continue
                gutter=4
                left=max(0,min(w-1,math.floor(min(us)*w)-gutter)); right=max(left+1,min(w,math.ceil(max(us)*w)+gutter))
                bottom=max(0,min(h-1,math.floor(min(vs)*h)-gutter)); upper=max(bottom+1,min(h,math.ceil(max(vs)*h)+gutter))
                if (right-left)*(upper-bottom)>=w*h*.95: continue
                pixels=np.empty(w*h*4,dtype=np.float32); image.pixels.foreach_get(pixels)
                crop=pixels.reshape(h,w,4)[bottom:upper,left:right].copy()
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
                crops.append({'source':image.name,'sourceSize':[w,h],'size':list(result.size),'pixelBoxBottomOrigin':[left,bottom,right,upper],'material':name,'node':node.name,'coordinateTransform':transform})
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
        bpy.ops.wm.save_as_mainfile(filepath=str(destination),compress=True)
        evidence={'source':str(source),'sourceSha256':source_hash,'sourceBytes':original_stat.st_size,'outputBytes':destination.stat().st_size,'centerX':args.x,'centerZ':args.z,'edge':args.edge,'metersPerPixel':args.mpp,'footprintMeters':args.edge*args.mpp,'cropBoundsBlenderXY':bounds,'heightRange':[low,top],'sunShadowHaloMeters':halo,'suns':suns,'before':before,'meshes':meshes,'images':images,'imageCrops':crops,'retainedImageSamples':retained,'objects':[{'name':o.name,'type':o.type} for o in scene.objects],'decodedRgbaMipBytes':sum(im['floatRgbaMipBytes'] for im in images),'gpuInitialized':False,'rendered':False,'devicePeakVerified':False,'fidelityGaps':['Indirect illumination and sun angular penumbra beyond the center-ray shadow halo are not bounded; matched CPU comparison pending.','Repeating detail images retain source resolution and coordinates.','GPU allocation and time bounds are not established.'],'threads':2,'device':'CPU'}
        evidence['imageReductions']=reductions
        evidence['haloMode']='directional-sun-angular-cone' if args.directional_halo else 'symmetric-center-ray'
        evidence['directionalCasterOffsetsXY']=directional
        if args.directional_halo:
            evidence['fidelityGaps'][0]='Directional bounds cover direct sun rays including angular penumbra using full source height range; indirect illumination beyond finite crop remains unbounded, and matched CPU comparison is pending.'
        with destination.open('rb') as handle:
            evidence['outputSha256']=hashlib.file_digest(handle,'sha256').hexdigest()
        evidence['materials']=[{'name':m.name,'nodes':[{'name':n.name,'type':n.type,'operation':getattr(n,'operation',None),'image':n.image.name if n.type=='TEX_IMAGE' and n.image else None} for n in m.node_tree.nodes],'links':[{'fromNode':l.from_node.name,'fromSocket':l.from_socket.name,'toNode':l.to_node.name,'toSocket':l.to_socket.name} for l in m.node_tree.links]} for m in bpy.data.materials if m.use_nodes]
        evidence['passes']=[{'name':l.name,'enabled':[p.identifier for p in l.bl_rna.properties if p.identifier.startswith('use_pass_') and getattr(l,p.identifier)]} for l in scene.view_layers]
        if reductions:
            evidence['fidelityGaps'][1]='Explicit lossy texture-cap variant: minification, wrap filtering, alpha/normal and color fidelity require matched CPU comparison; not accepted imagery.'
        (output/'manifest.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        if source.stat().st_size!=original_stat.st_size or source.stat().st_mtime_ns!=original_stat.st_mtime_ns:
            raise RuntimeError('Source changed during preprocessing')
        print(json.dumps({'output':str(destination),'meshCount':len(meshes),'triangles':sum(m['triangles'] for m in meshes),'imageCount':len(images),'imageBytes':evidence['decodedRgbaMipBytes'],'crops':len(crops),'halo':halo}),flush=True)

if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    main(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:])
