import sys
import os
sys.path.append('.')

import numpy as np
import jax.numpy as jnp
import jax
from jax3dp3.model import make_scoring_function
from jax3dp3.rendering import render_planes
from jax3dp3.distributions import VonMisesFisher
from jax3dp3.enumerations import get_rotation_proposals
from jax3dp3.shape import get_cube_shape, get_corner_shape
from jax3dp3.viz.img import save_depth_image
import time
from PIL import Image
from scipy.spatial.transform import Rotation as R
import matplotlib.pyplot as plt
import cv2

### Setup

OBJ_DEFAULT_POSE = TEMPLATE_DEFAULT_POSE = jnp.array([
    [1.0, 0.0, 0.0, -1.0],   
    [0.0, 1.0, 0.0, -1.0],   
    [0.0, 0.0, 1.0, 2.0],   
    [0.0, 0.0, 0.0, 1.0],   
    ]
) 

h, w, fx_fy, cx_cy = (
    100,
    100,
    jnp.array([50.0, 50.0]),
    jnp.array([50.0, 50.0]),
)

r = 0.1
outlier_prob = 0.01
pixel_smudge = 0



### Generate GT images

num_frames = 50

gt_poses = [
    jnp.array([
    [1.0, 0.0, 0.0, -1.0],   
    [0.0, 1.0, 0.0, -1.0],   
    [0.0, 0.0, 1.0, 2.0],   
    [0.0, 0.0, 0.0, 1.0],   
    ]
)
]
rot = R.from_euler('zyx', [1.0, -0.1, -2.0], degrees=True).as_matrix()
delta_pose =     jnp.array([
    [1.0, 0.0, 0.0, 0.15],   
    [0.0, 1.0, 0.0, 0.05],   
    [0.0, 0.0, 1.0, 0.02],   
    [0.0, 0.0, 0.0, 1.0],   
    ]
)
delta_pose = delta_pose.at[:3,:3].set(jnp.array(rot))

for t in range(num_frames):
    gt_poses.append(gt_poses[-1].dot(delta_pose))
gt_poses = jnp.stack(gt_poses)
print("gt_poses.shape", gt_poses.shape)

shape = get_cube_shape(0.5)

render_planes_jit = jax.jit(lambda p: render_planes(p,shape,h,w,fx_fy,cx_cy))
render_planes_parallel_jit = jax.jit(jax.vmap(lambda p: render_planes(p,shape,h,w,fx_fy,cx_cy)))
gt_images = render_planes_parallel_jit(gt_poses)



### Define scoring functions

scorer = make_scoring_function(shape, h, w, fx_fy, cx_cy ,r, outlier_prob)
scorer_parallel = jax.vmap(scorer, in_axes = (0, None))



### Make a corner template (Not used in inference for now)
corner_shape = get_corner_shape(0.15)
render_corner_jit = jax.jit(lambda p, crnr_shape: render_planes(p, crnr_shape, h, w, fx_fy, cx_cy))
corner_template = render_corner_jit(TEMPLATE_DEFAULT_POSE, corner_shape) 


### Choose gt frame(s) to test; currently one tested
def grayscale(arr, max_depth=10.0):
    return (np.array(arr) / max_depth * 255.0).astype('uint8')

test_gt_images = []
test_frame_idx = 10

observed = gt_images[test_frame_idx, :, :, :]

save_depth_image(observed[:,:,2], 5.0, "gt_img.png")

test_gt_images.append(observed)
test_gt_images = jnp.stack(test_gt_images)
observed_viz = grayscale(observed)
observed_depth = observed[:, :, 2]
# Initiate ORB detector
orb = cv2.ORB_create()
# find the keypoints / descriptors with ORB
kp, des = orb.detectAndCompute(observed_viz,None)
observed_depth_kp = cv2.drawKeypoints(observed_viz[:, :, 2], kp, None, color=(0,255,0), flags=0)  # just for viz
# plt.imshow(observed_depth_kp), plt.show()

if len(kp) == 0:
    gx, gy, gz = -1.0, -1.0, 2.0
else:
    sample_keypoint = kp[0].pt  # take a sample keypoint to location match
    t_col, t_row = sample_keypoint
    t_col, t_row = int(t_col), int(t_row); print(t_col, t_row)
    gx, gy, gz = observed[t_row, t_col, :3]

translation_proposal = jnp.array([
    [0.0, 0.0, 0.0, gx],   
    [0.0, 0.0, 0.0, gy],   
    [0.0, 0.0, 0.0, gz],   
    [0.0, 0.0, 0.0, 0.0],   
    ]
)  # instead of jointly inferring over translation/rotation, we will fix the object location to [gx,gy,gz]
print("translation proposal=", translation_proposal)


### Enumerative inference over rotation proposals
rotation_proposals = get_rotation_proposals(100, 0.25)
print("enumerating over ", rotation_proposals.shape[0], " rotations")


NUM_BATCHES = rotation_proposals.shape[0] // 100   # anything greater than 300 leads to memory allocation err 
rotation_proposals_batches = jnp.array(jnp.split(rotation_proposals, NUM_BATCHES))

def inference_frame(gt_image):  # scan over batches of rotation proposals for single image
    def enum_infer_batch_scan(carry, rotation_proposals_batch):  # use global translation proposal/gt_test_images
        # score over the selected rotation proposals
        proposals = rotation_proposals_batch + translation_proposal
        weights_new = scorer_parallel(proposals, gt_image)
        x, x_weight = proposals[jnp.argmax(weights_new)], jnp.max(weights_new)

        # prev_x, prev_weight = carry
        new_x, new_weight = jax.lax.cond(carry[-1] > jnp.max(weights_new), lambda: carry, lambda: (x, x_weight))

        return (new_x, new_weight), None  # return highest weight pose proposal encountered so far
    best_prop, _ = jax.lax.scan(enum_infer_batch_scan, (jnp.empty((4,4)), jnp.NINF), rotation_proposals_batches)
    return best_prop

inference_frames = jax.vmap(inference_frame, in_axes=(0)) # vmap over images
inference_frames_jit = jax.jit(inference_frames)


### Visualize max likelihood proposal
_ = inference_frames_jit(jnp.empty(test_gt_images.shape))  # a (num_images x 4 x 4) arr of best pose estim for each image frame
start = time.time()
all_best_poses, _ = inference_frames_jit(test_gt_images)  # time
end = time.time()
elapsed = end - start
print("Time elapsed:", elapsed)
print("FPS:", len(test_gt_images) / elapsed)
print("best poses:", all_best_poses)

# predicted_location = render_planes_jit(all_best_poses[0])
# plt.imshow(grayscale(predicted_location[:, :, 2])); plt.show()  # visualize shape
best_image = render_planes_jit(all_best_poses[0])
save_depth_image(best_image[:,:,2], 5.0, "img.png")

### TODO: once pose inference tuned reasonably, add a final correction step based on icp...




from IPython import embed; embed()
