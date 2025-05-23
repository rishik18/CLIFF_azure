# Copyright (C) 2022. Huawei Technologies Co., Ltd. All rights reserved.

# This program is free software; you can redistribute it and/or modify it
# under the terms of the MIT license.

# This program is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the MIT License for more details.

import torch
from collections import OrderedDict
import os
import glob
from tqdm import tqdm
import cv2
import subprocess


def strip_prefix_if_present(state_dict, prefix):
    keys = sorted(state_dict.keys())
    if not any(key.startswith(prefix) for key in keys):
        return state_dict
    stripped_state_dict = OrderedDict()
    for key, value in state_dict.items():
        stripped_state_dict[key.replace(prefix, "")] = value
    return stripped_state_dict


def cam_crop2full(crop_cam, center, scale, full_img_shape, focal_length):
    """
    convert the camera parameters from the crop camera to the full camera
    :param crop_cam: shape=(N, 3) weak perspective camera in cropped img coordinates (s, tx, ty)
    :param center: shape=(N, 2) bbox coordinates (c_x, c_y)
    :param scale: shape=(N, 1) square bbox resolution  (b / 200)
    :param full_img_shape: shape=(N, 2) original image height and width
    :param focal_length: shape=(N,)
    :return:
    """
    img_h, img_w = full_img_shape[:, 0], full_img_shape[:, 1]
    cx, cy, b = center[:, 0], center[:, 1], scale * 200
    w_2, h_2 = img_w / 2., img_h / 2.
    bs = b * crop_cam[:, 0] + 1e-9
    tz = 2 * focal_length / bs
    tx = (2 * (cx - w_2) / bs) + crop_cam[:, 1]
    ty = (2 * (cy - h_2) / bs) + crop_cam[:, 2]
    full_cam = torch.stack([tx, ty, tz], dim=-1)
    return full_cam


def full2crop_cam(full_cam, center, b, camera_center_tensor, focal_length):
    """
    Invert cam_crop2full:
      full_cam:    (N,3) tensor [tx_full, ty_full, tz]
      center:      (N,2) tensor [c_x, c_y]
      scale:       (N,1) tensor b/200
      full_img_shape: (N,2) tensor [img_h, img_w]
      focal_length:  (N,) tensor
    Returns:
      crop_cam:    (N,3) tensor [s, tx_crop, ty_crop]
    """
    cx, cy       = center[:, 0], center[:, 1]

    #b            = scale * 200.0             # b = scale * 200
    w2, h2       = camera_center_tensor[:,0],camera_center_tensor[:,1]
    #w2,h2= 960,540
    tx_full, ty_full, tz = full_cam.unbind(dim=-1)

    # recover bs = b * s
    # from tz = 2*f / bs  ==>  bs = 2*f / tz
    eps = 1e-9
    bs = 2.0 * focal_length / (tz + eps)

    # then s = bs / b
    s = bs / b

    # and
    # tx_full = 2*(cx - w2)/bs + tx_crop
    # ty_full = 2*(cy - h2)/bs + ty_crop
    tx_crop = tx_full - 2.0 * (cx - w2) / bs
    ty_crop = ty_full - 2.0 * (cy - h2) / bs

    crop_cam = torch.stack([s, tx_crop, ty_crop], dim=-1)
    return crop_cam


def video_to_images(vid_file, img_folder=None):
    command = ['ffmpeg',
               '-i', vid_file,
               '-f', 'image2',
               '-v', 'error',
               f'{img_folder}/%06d.png']
    print(f'Running \"{" ".join(command)}\"')
    subprocess.call(command)
    print(f'Images saved to \"{img_folder}\"')


def images_to_video(img_dir, video_path, frame_rate=30):
    img_list = glob.glob(os.path.join(img_dir, '*.jpg'))
    img_list.extend(glob.glob(os.path.join(img_dir, '*.png')))
    img_list.sort()

    img_exp = cv2.imread(img_list[0])
    rows, cols, ch = img_exp.shape
    video = cv2.VideoWriter(video_path,
                            cv2.VideoWriter_fourcc('m', 'p', '4', 'v'),
                            frame_rate, (cols, rows))

    for img_path in tqdm(img_list):
        video.write(cv2.imread(img_path))
    video.release()


def estimate_focal_length(img_h, img_w):
    return (img_w * img_w + img_h * img_h) ** 0.5  # fov: 55 degree
