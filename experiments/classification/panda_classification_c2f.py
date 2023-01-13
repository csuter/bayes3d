import numpy as np
import os
import jax
import jax.numpy as jnp
import trimesh
import time
import pickle
import jax3dp3.transforms_3d as t3d
import jax3dp3

scene_num = 1
panda_dataset_path = os.path.join(jax3dp3.utils.get_assets_dir(), "panda_dataset")
file = open(os.path.join(panda_dataset_path, f"scene_{scene_num}.pkl"),'rb')
all_data = pickle.load(file)
file.close()
t = -1
data = all_data[t]
print(data.keys())

rgb = data["rgb"]
rgb_viz = jax3dp3.viz.get_rgb_image(rgb, 255.0)
rgb_viz.save("rgb.png")
depth = data["depth"] / 1000.0
jax3dp3.viz.get_depth_image(depth, max=3000.0).save("depth.png")
K = data["intrinsics"][0]
orig_h,orig_w = depth.shape
orig_fx, orig_fy, orig_cx, orig_cy = K[0,0],K[1,1],K[0,2],K[1,2]
near = 0.01
far = 5.0

scaling_factor = 0.25
max_depth = far
h,w,fx,fy,cx,cy = jax3dp3.camera.scale_camera_parameters(orig_h,orig_w,orig_fx,orig_fy,orig_cx,orig_cy, scaling_factor)
depth = jax3dp3.utils.resize(depth, h, w)

jax3dp3.setup_renderer(h, w, fx, fy, cx, cy, near, far)

model_dir = os.path.join(jax3dp3.utils.get_assets_dir(),"models")
model_names = np.array(os.listdir(model_dir))
model_box_dims = []
for model in model_names:
    mesh = trimesh.load(os.path.join(jax3dp3.utils.get_assets_dir(),"models/{}/textured_simple.obj".format(model)))
    mesh = jax3dp3.mesh.center_mesh(mesh)
    model_box_dims.append(jax3dp3.utils.axis_aligned_bounding_box(mesh.vertices)[0])
    jax3dp3.load_model(mesh)
model_box_dims = jnp.array(model_box_dims)

cam_pose = jnp.eye(4)

### segment the image
gt_image_full = t3d.depth_to_point_cloud_image(depth, fx,fy,cx,cy)
jax3dp3.viz.save_depth_image(gt_image_full[:,:,2], "gt_image_full.png", max=far)
gt_point_cloud_full = t3d.point_cloud_image_to_points(gt_image_full)
table_pose, table_dims = jax3dp3.utils.find_table_pose_and_dims(gt_point_cloud_full[gt_point_cloud_full[:,2] < far, :], 
ransac_threshold=0.001, inlier_threshold=0.002, segmentation_threshold=0.004)

gt_image_above_table = gt_image_full * (t3d.apply_transform(gt_image_full, jnp.linalg.inv(table_pose))[:,:,2] > 0.02)[:,:,None]
jax3dp3.viz.save_depth_image(gt_image_above_table[:,:,2], "gt_image_above_table.png", max=far)

segmentation_img = jax3dp3.utils.segment_point_cloud_image(gt_image_above_table, threshold=0.01, min_points_in_cluster=30)
jax3dp3.viz.save_depth_image(segmentation_img + 1, "seg.png", max=segmentation_img.max() + 1)
unique, counts =  np.unique(segmentation_img, return_counts=True)

table_face_param = 2
table_surface_plane_pose = jax3dp3.scene_graph.get_contact_plane(table_pose, table_dims, table_face_param)

segmentation_idx_to_do_pose_estimation_for = unique[unique != -1]
print(segmentation_idx_to_do_pose_estimation_for)

object_indices = list(range(len(model_names)))
outlier_prob, outlier_volume = 0.01, 20**3

grid_width = 0.1
contact_param_sweep, face_param_sweep = jax3dp3.scene_graph.enumerate_contact_and_face_parameters(
    -grid_width, -grid_width, 0.0, +grid_width, +grid_width, jnp.pi, 
    5, 5, 10,
    jnp.arange(6)
)

grid_width = 0.05
contact_param_sweep_2, face_param_sweep_2 = jax3dp3.scene_graph.enumerate_contact_and_face_parameters(
    -grid_width, -grid_width, 0.0, +grid_width, +grid_width, jnp.pi*2, 
    5, 5, 10,
    jnp.arange(6)
)

grid_width = 0.02
contact_param_sweep_3, face_param_sweep_3 = jax3dp3.scene_graph.enumerate_contact_and_face_parameters(
    -grid_width, -grid_width, 0.0, +grid_width, +grid_width, jnp.pi, 
    5, 5, 10,
    jnp.arange(6)
)

contact_param_sched = [contact_param_sweep, contact_param_sweep_2, contact_param_sweep_3]
face_param_sched = [face_param_sweep, face_param_sweep_2, face_param_sweep_3]
likelihood_r_sched = [0.1, 0.05, 0.02]

def get_pose_estimation_for_segmentation(seg_id):
    gt_image_masked = gt_image_above_table * (segmentation_img == seg_id)[:,:,None]
    gt_img_viz = jax3dp3.viz.get_depth_image(gt_image_masked[:,:,2],  max=max_depth)
    gt_img_viz.save("imgs/gt_image_masked.png")

    gt_img_complement = gt_image_above_table * (segmentation_img != seg_id)[:,:,None]
    gt_img_complement_viz = jax3dp3.viz.get_depth_image(gt_img_complement[:,:,2],  max=max_depth)
    gt_img_complement_viz.save("imgs/gt_img_complement.png")


    points_in_table_ref_frame =  t3d.apply_transform(t3d.point_cloud_image_to_points(gt_image_masked), 
        t3d.inverse(table_surface_plane_pose).dot(cam_pose))
    point_seg = jax3dp3.utils.segment_point_cloud(points_in_table_ref_frame, 0.1)
    points_filtered = points_in_table_ref_frame[point_seg == jax3dp3.utils.get_largest_cluster_id_from_segmentation(point_seg)]
    center_x, center_y, _ = ( points_filtered.min(0) + points_filtered.max(0))/2
    
    top_k = 5

    start = time.time()
    results = jax3dp3.c2f.c2f_contact_parameters(
        jnp.array([center_x, center_y, 0.0]),
        contact_param_sched, face_param_sched, likelihood_r_sched=likelihood_r_sched,
        contact_plane_pose=table_surface_plane_pose,
        gt_image_masked=gt_image_masked, gt_img_complement=gt_img_complement,
        model_box_dims=model_box_dims,
        outlier_prob=outlier_prob,
        outlier_volume=outlier_volume,
        top_k=top_k
    )
    print(results[0])
    end= time.time()
    print ("Time elapsed:", end - start)


    all_scores = jnp.array([item[0] for item in results])
    all_idxs = jnp.array([item[1] for item in results])
    all_poses = jnp.array([item[-1] for item in results])


    order = np.argsort(-all_scores)
    best_idx = all_idxs[order[0]]
    best_score = all_scores[order[0]]
    best_pose = all_poses[order[0]]
    pred_rendered_img = jax3dp3.render_single_object(best_pose, best_idx)

    
    r_overlap_check = 0.05
    overlap = jax3dp3.likelihood.threedp3_likelihood_get_counts(gt_image_masked, pred_rendered_img, r_overlap_check)
    pred_viz = jax3dp3.viz.get_depth_image(
        pred_rendered_img[:,:,2], max=max_depth
    )
    overlay = jax3dp3.viz.overlay_image(jax3dp3.viz.resize_image(rgb_viz, h,w), pred_viz,alpha=0.5)
    
    bottom_text_string = "Object Class : Score\n"
    for i in order:
        bottom_text_string += (
            "{} : {}\n".format(model_names[all_idxs[i]], all_scores[i])
        )


    bottom_text_string += "\n"
    filename = f"imgs/scene_{scene_num}_seg_id_{seg_id}.png"

    jax3dp3.viz.multi_panel([gt_img_viz, pred_viz, overlay], 
        labels=[
            "Ground Truth", 
            "Prediction\nScore: {:.2f} {:s}".format(best_score, model_names[best_idx]), 
            "Overlap:\n{}/{}, {}/{}".format(
                *overlap
            )
        ],
        bottom_text=bottom_text_string,
        middle_width=50,
    ).save(filename)

for seg_id in segmentation_idx_to_do_pose_estimation_for:
    get_pose_estimation_for_segmentation(seg_id)
    # from IPython import embed; embed()

from IPython import embed; embed()



# for i in range(1,16):
#     file = open("./panda_dataset/scene_{}.pkl".format(i),'rb')
#     all_data = pickle.load(file)
#     file.close()

#     t = -1
#     data = all_data[t]
#     print(data.keys())

#     rgb = data["rgb"]
#     rgb_viz = jax3dp3.viz.get_rgb_image(rgb, 255.0)
#     rgb_viz.save("rgb_{}.png".format(i))
