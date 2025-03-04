import jax.numpy as jnp
import bayes3d as j
import trimesh
import os
import numpy as np
import trimesh

# --- creating the ycb dir from the working directory
bop_ycb_dir = os.path.join(j.utils.get_assets_dir(), "bop/ycbv")
rgbd, gt_ids, gt_poses, masks = j.ycb_loader.get_test_img('52', '1', bop_ycb_dir)

# --- creating the model dir from the working directory
model_dir = os.path.join(j.utils.get_assets_dir(), "ycb_video_models/models")
mesh_paths = []
model_names = j.ycb_loader.MODEL_NAMES
offset_poses = []
for name in model_names:
    mesh_path = os.path.join(model_dir,name,"textured.obj")
    _, pose = j.mesh.center_mesh(trimesh.load(mesh_path), return_pose=True)
    offset_poses.append(pose)
    mesh_paths.append(
        mesh_path
    )



rgbd, gt_ids, gt_poses, masks = j.ycb_loader.get_test_img(
    # '48', '1', "/home/nishadgothoskar/data/bop/ycbv"
    # '52', '1', "/home/nishadgothoskar/data/bop/ycbv"
    '55', '22', bop_ycb_dir
)
intrinsics = j.Intrinsics(
    rgbd.intrinsics.height, rgbd.intrinsics.width,
    rgbd.intrinsics.fx, rgbd.intrinsics.fx,
    rgbd.intrinsics.width/2, rgbd.intrinsics.height/2,
    rgbd.intrinsics.near, rgbd.intrinsics.far
)

paths = []
for i in gt_ids:
    paths.append(mesh_paths[i])

intrinsics = j.Intrinsics(
    rgbd.intrinsics.height, rgbd.intrinsics.width,
    rgbd.intrinsics.fx, rgbd.intrinsics.fx,
    rgbd.intrinsics.width/2, rgbd.intrinsics.height/2,
    rgbd.intrinsics.near, rgbd.intrinsics.far
)

poses = []
for i in range(len(gt_ids)):
    poses.append(
        gt_poses[i] @ j.t3d.inverse_pose(offset_poses[gt_ids[i]])
    )
poses = jnp.array(poses)

mesh_paths = paths
rgbds = j.kubric_interface.render_multiobject_parallel(paths, poses[:,None,...], intrinsics, scaling_factor=1.0, lighting=5.0)


T = 0
rgbd = rgbds[0]
rgb = rgbds[T].rgb
seg = rgbds[T].segmentation
depth = rgbds[T].depth
j.get_rgb_image(rgb).save("rgb.png")

# import bayes3d.posecnn_densefusion
# densefusion = j.posecnn_densefusion.DenseFusion()
# results = densefusion.get_densefusion_results(rgbd.rgb, rgbd.depth, rgbd.intrinsics, scene_name="1")
# print(results)



rgba = jnp.array(j.viz.add_rgba_dimension(rgb))
rgba = rgba.at[seg == 0, 3].set(0.0)

rgb_viz = j.get_rgb_image(rgba)
depth_viz = j.get_depth_image(depth, max=intrinsics.far)
seg_viz = j.get_depth_image(seg, max=seg.max())


renderer = j.Renderer(intrinsics)
for p in paths:
    renderer.add_mesh_from_file(p)
img = renderer.render_multiobject(poses, jnp.arange(len(paths)))
depth_viz2 = j.get_depth_image(img[:,:,2], max=intrinsics.far)
j.multi_panel(
    [
        rgb_viz,
        depth_viz,
        seg_viz,
        depth_viz2
    ]
).save("test_kubric.png")

# j.setup_visualizer()
# j.show_cloud("1", j.t3d.unproject_depth(depth, intrinsics).reshape(-1,3),color=j.RED)
# j.show_cloud("2", j.t3d.unproject_depth(img[:,:,2], intrinsics).reshape(-1,3), color=j.BLUE)

from IPython import embed; embed()