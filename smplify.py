import os
import torch

from models.smpl import SMPL
from common import constants
from losses import camera_fitting_loss, body_fitting_loss

# For the GMM prior, we use the GMM implementation of SMPLify-X
# https://github.com/vchoutas/smplify-x/blob/master/smplifyx/prior.py
from prior import MaxMixturePrior

class SMPLify():
    """Implementation of single-stage SMPLify.""" 
    def __init__(self, 
                 step_size=1e-2,
                 batch_size=32,
                 num_iters=100,
                 focal_length=5000,
                 device=torch.device('cuda')):

        # Store options
        self.device = device
        self.focal_length = focal_length
        self.step_size = step_size

        # Ignore the the following joints for the fitting process
        #ign_joints = ['OP Neck', 'OP RHip', 'OP LHip', 'Right Hip', 'Left Hip']
        
        ign_joints =JOINT_NAMES = [ 'OP Neck',
                                    
                                    'OP LSmallToe',
                                    'OP LHeel',
                                    'OP RHip', 'OP LHip', 'Right Hip', 'Left Hip',
                                    'OP RSmallToe',
                                    'OP RHeel',
                                    'Neck (LSP)',
                                    'Top of Head (LSP)',
                                    'Pelvis (MPII)',
                                    'Spine (H36M)',
                                    'Jaw (H36M)',
                                    'Head (H36M)',
                                    ]
        self.ign_joints = [constants.JOINT_IDS[i] for i in ign_joints]
        self.num_iters = num_iters
        # GMM pose prior
        self.pose_prior = MaxMixturePrior(prior_folder='data',
                                          num_gaussians=8,
                                          dtype=torch.float32).to(device)
        # Load SMPL model
        self.smpl = SMPL(constants.SMPL_MODEL_DIR, batch_size=1).to(device)
        
    def __call__(self, init_pose, init_betas, init_cam_t, camera_center, keypoints_2d):
        """Perform body fitting.
        Input:
            init_pose: SMPL pose estimate
            init_betas: SMPL betas estimate
            init_cam_t: Camera translation estimate
            camera_center: Camera center location
            keypoints_2d: Keypoints used for the optimization
        Returns:
            vertices: Vertices of optimized shape
            joints: 3D joints of optimized shape
            pose: SMPL pose parameters of optimized shape
            betas: SMPL beta parameters of optimized shape
            camera_translation: Camera translation
            reprojection_loss: Final joint reprojection loss
        """
        batch_size = init_pose.shape[0]

        # Make camera translation a learnable parameter
        camera_translation = init_cam_t.clone()

        # Get joint confidence
        joints_2d = keypoints_2d[:, :, :2]
        joints_conf = keypoints_2d[:, :, -1]
        
        # Split SMPL pose to body pose and global orientation
        body_pose = init_pose[:, 3:].detach().clone()
        global_orient = init_pose[:, :3].detach().clone()
        betas = init_betas.detach().clone()

        # Step 1: Optimize camera translation and body orientation
        # Optimize only camera translation and body orientation
        body_pose.requires_grad=False
        betas.requires_grad=False
        global_orient.requires_grad=True
        camera_translation.requires_grad = True

        camera_opt_params = [global_orient, camera_translation]
        camera_optimizer = torch.optim.Adam(camera_opt_params, lr=self.step_size, betas=(0.9, 0.999))

        for i in range(self.num_iters):
            
            smpl_output = self.smpl(betas=betas,
                                    body_pose=body_pose,
                                    global_orient=global_orient,
                                    pose2rot=True,
                                    transl=camera_translation)
            
            model_joints = smpl_output.joints
            loss = camera_fitting_loss(model_joints, camera_translation,
                                       init_cam_t, camera_center,
                                       joints_2d, joints_conf, focal_length=self.focal_length)
            camera_optimizer.zero_grad()
            loss.backward()
            camera_optimizer.step()
        
        # Fix camera translation after optimizing camera
        camera_translation.requires_grad = False

        # Step 2: Optimize body joints
        # Optimize only the body pose and global orientation of the body
        body_pose.requires_grad=True
        betas.requires_grad=True
        global_orient.requires_grad=True
        camera_translation.requires_grad = False
        body_opt_params = [body_pose, betas, global_orient]

        # For joints ignored during fitting, set the confidence to 0
        joints_conf[:, self.ign_joints] = 0.
        
        body_optimizer = torch.optim.Adam(body_opt_params, lr=self.step_size, betas=(0.9, 0.999))
        for i in range(self.num_iters):

            smpl_output = self.smpl(betas=betas,
                                    body_pose=body_pose,
                                    global_orient=global_orient,
                                    pose2rot=True,
                                    transl=camera_translation)
            
            model_joints = smpl_output.joints
            loss = body_fitting_loss(body_pose, betas, model_joints, camera_translation, camera_center,
                                     joints_2d, joints_conf, self.pose_prior,
                                     focal_length=self.focal_length)
            body_optimizer.zero_grad()
            loss.backward()
            body_optimizer.step()

        # Get final loss value
        with torch.no_grad():

            smpl_output = self.smpl(betas=betas,
                                    body_pose=body_pose,
                                    global_orient=global_orient,
                                    pose2rot=True,
                                    transl=camera_translation)
            
            model_joints = smpl_output.joints
            reprojection_loss = body_fitting_loss(body_pose, betas, model_joints, camera_translation, camera_center,
                                                  joints_2d, joints_conf, self.pose_prior,
                                                  focal_length=self.focal_length,
                                                  output='reprojection')

        vertices = smpl_output.vertices.detach()
        joints = smpl_output.joints.detach()
        pose = torch.cat([global_orient, body_pose], dim=-1).detach()
        betas = betas.detach()
        faces = self.smpl.faces
        return vertices, joints, pose, betas, camera_translation, reprojection_loss, faces
