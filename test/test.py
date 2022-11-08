import numpy as np
import jax.numpy as jnp
import jax
from jax3dp3.rendering import render_planes
from jax3dp3.distributions import gaussian_vmf
from jax3dp3.utils import (
    make_centered_grid_enumeration_3d_points,
    depth_to_coords_in_camera
)
from jax3dp3.transforms_3d import quaternion_to_rotation_matrix
from jax3dp3.shape import get_cube_shape
import time
from PIL import Image
from scipy.spatial.transform import Rotation as R
import matplotlib.pyplot as plt
import cv2
from jax3dp3.likelihood import threedp3_likelihood

h, w, fx_fy, cx_cy = (
    300,
    300,
    jnp.array([200.0, 200.0]),
    jnp.array([150.0, 150.0]),
)
r = 0.1
outlier_prob = 0.01

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
    [1.0, 0.0, 0.0, 0.09],   
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
print("gt_images.shape", gt_images.shape)
print((gt_images[0,:,:,-1] > 0 ).sum())

key = jax.random.PRNGKey(3)
def scorer(x, obs):
    rendered_image = render_planes(x, shape,h,w,fx_fy,cx_cy)
    weight = threedp3_likelihood(obs, rendered_image, r, outlier_prob)
    return weight

score = scorer(gt_poses[0,:,:], gt_images[0,:,:,:])
print("score", score)

scorer_parallel = jax.vmap(scorer, in_axes = (0, None))

f_jit = jax.jit(jax.vmap(lambda t:     jnp.vstack(
        [jnp.hstack([jnp.eye(3), t.reshape(3,-1)]), jnp.array([0.0, 0.0, 0.0, 1.0])]
    )))
pose_deltas = f_jit(make_centered_grid_enumeration_3d_points(0.2, 0.2, 0.2, 5, 5, 5))
print("grid ", pose_deltas.shape)
key, *sub_keys = jax.random.split(key, pose_deltas.shape[0] + 1)
sub_keys_translation = jnp.array(sub_keys)

key, *sub_keys = jax.random.split(key, 100)
sub_keys = jnp.array(sub_keys)
f_jit = jax.jit(jax.vmap(lambda key: gaussian_vmf(key, 0.00001, 800.0)))
rotation_deltas = f_jit(sub_keys)
print("grid ", rotation_deltas.shape)
key, *sub_keys = jax.random.split(key, rotation_deltas.shape[0] + 1)
sub_keys_orientation = jnp.array(sub_keys)


def _inner(x, gt_image):
    for _ in range(1):
        proposals = jnp.einsum("ij,ajk->aik", x, pose_deltas)
        weights_new = scorer_parallel(proposals, gt_image)
        x = proposals[jnp.argmax(weights_new)]

        proposals = jnp.einsum("ij,ajk->aik", x, rotation_deltas)
        weights_new = scorer_parallel(proposals, gt_image)
        x = proposals[jnp.argmax(weights_new)]

    return x, x


def inference(init_pos, gt_images):
    return jax.lax.scan(_inner, init_pos, gt_images)


inference_jit = jax.jit(inference)

a = inference_jit(gt_poses[0], gt_images)

start = time.time()
_, inferred_poses = inference_jit(gt_poses[0], gt_images);
end = time.time()
print ("Time elapsed:", end - start)
print ("FPS:", gt_poses.shape[0] / (end - start))

cm = plt.get_cmap('turbo')

max_depth = 20.0
images = []
middle_width = 50
for i in range(gt_images.shape[0]):
    dst = Image.new(
        "RGBA", (2 * w + middle_width, h)
    )
    obsedved_image_pil = Image.fromarray(
        (cm(np.array(gt_images[i,:, :, 2]) / max_depth) * 255.0).astype(np.int8), mode="RGBA"
    )

    dst.paste(
        obsedved_image_pil,
        (0, 0),
    )

    pose = inferred_poses[i]
    rendered_image = render_planes_jit(pose)
    rendered_image_pil = Image.fromarray(
        (cm(np.array(rendered_image[:, :, 2]) / max_depth) * 255.0).astype(np.int8), mode="RGBA"
    )

    dst.paste(
        rendered_image_pil,
        (w + middle_width, 0),
    )
    images.append(dst)



images[0].save(
    fp="out.gif",
    format="GIF",
    append_images=images,
    save_all=True,
    duration=100,
    loop=0,
)