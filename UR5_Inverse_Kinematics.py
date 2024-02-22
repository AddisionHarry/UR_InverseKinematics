import pybullet
import h5py
from tqdm import trange
import numpy
from scipy.spatial.transform import Rotation

from math_utils import unwind_angles, point_transfer_scale
from pybullet_draw_display import disp_human_demonstrate

global_z_offset = 1.0


class UR5_Inverse_Kinematics_Simulation:
    def __init__(self, urdf_file: str):
        # Connect the client
        self.client = pybullet.connect(pybullet.GUI)
        pybullet.configureDebugVisualizer(pybullet.COV_ENABLE_GUI, 0)
        # Add source path
        import pybullet_data

        pybullet.setAdditionalSearchPath(pybullet_data.getDataPath())
        # Load land
        pybullet.loadURDF("plane.urdf")
        # Load robot
        self.robot_id = pybullet.loadURDF(fileName=urdf_file, basePosition=[0, 0, global_z_offset])
        # Set camera
        pybullet.resetDebugVisualizerCamera(
            cameraDistance=1.8,
            cameraYaw=95,
            cameraPitch=-20,
            cameraTargetPosition=[0, 0, 0.5 + global_z_offset],
        )
        # Get joints available
        self.all_joints_num = pybullet.getNumJoints(self.robot_id)
        self.end_effector_joint_index = (7, 16)
        self.available_joints_num = len(self.available_joints_indices)

    @property
    def available_joints_indices(self) -> list[int]:
        # [2, 3, 4, 5, 6, 7, 11, 12, 13, 14, 15, 16]
        return [
            i
            for i in range(self.all_joints_num)
            if pybullet.getJointInfo(bodyUniqueId=self.robot_id, jointIndex=i)[2]
               != pybullet.JOINT_FIXED
        ]

    @property
    def available_joint_names(self) -> list[str]:
        # ['left_shoulder_pan_joint', 'left_shoulder_lift_joint', 'left_elbow_joint',
        #  'left_wrist_1_joint', 'left_wrist_2_joint', 'left_wrist_3_joint',
        #  'right_shoulder_pan_joint', 'right_shoulder_lift_joint', 'right_elbow_joint',
        #  'right_wrist_1_joint', 'right_wrist_2_joint', 'right_wrist_3_joint']
        return [
            str(
                pybullet.getJointInfo(bodyUniqueId=self.robot_id, jointIndex=_joint)[1]
            )[2:-1]
            for _joint in self.available_joints_indices
        ]

    @property
    def arm_base_position(self) -> (list[float], list[float]):
        return self.get_link_position(2), self.get_link_position(11)

    @property
    def ee_orientation_quaternion(self) -> list[list[float]]:
        return [self.get_link_orientation_quaternion(ee) for ee in self.end_effector_joint_index]

    def get_joint_angle(self, joint_index: int) -> float:
        return pybullet.getJointState(self.robot_id, joint_index)[0]

    def get_link_position(self, link_index: int) -> list[float]:
        return pybullet.getLinkState(self.robot_id, link_index, computeForwardKinematics=True)[4]

    def get_link_orientation_quaternion(self, link_index: int) -> list[float]:
        return pybullet.getLinkState(self.robot_id, link_index, computeForwardKinematics=True)[5]

    def step_simulation(self, joint_angles: list[float]) -> None:
        if joint_angles:
            pybullet.setJointMotorControlArray(
                bodyUniqueId=self.robot_id,
                jointIndices=self.available_joints_indices,
                controlMode=pybullet.POSITION_CONTROL,
                targetPositions=joint_angles,
            )
        pybullet.stepSimulation(self.client)

    def __calculate_inverse_kinematics_without_orientation(
            self, target_joints_indices: list[float], target_positions: list[list]
    ) -> list[float]:
        return list(
            pybullet.calculateInverseKinematics2(
                bodyUniqueId=self.robot_id,
                endEffectorLinkIndices=target_joints_indices,
                targetPositions=target_positions,
            )
        )

    def __calculate_inverse_kinematics_given_orientation(
            self, target_positions: list[list[float]], target_orientations: list[list[float]],
    ) -> list[float]:
        res = [
            list(
                pybullet.calculateInverseKinematics(
                    bodyUniqueId=self.robot_id,
                    endEffectorLinkIndex=self.end_effector_joint_index[0],
                    targetPosition=target_positions[0],
                    targetOrientation=target_orientations[0]
                )
            ),
            list(
                pybullet.calculateInverseKinematics(
                    bodyUniqueId=self.robot_id,
                    endEffectorLinkIndex=self.end_effector_joint_index[1],
                    targetPosition=target_positions[1],
                    targetOrientation=target_orientations[1]
                )
            )
        ]
        return (
                res[0][: int(self.available_joints_num / 2)]
                + res[1][int(self.available_joints_num / 2):]
        )

    def calc_inverse_kinematics(
            self, target_positions: list[list[float]], target_orientations: list[list[float]],
    ) -> list[float]:
        # Arm inverse kinematics
        _angles = self.__calculate_inverse_kinematics_without_orientation(
            target_joints_indices=[3, 5, 7, 12, 14, 16], target_positions=target_positions
        )

        def get_numpy_matrix_from_quaternion(quaternion):
            return numpy.matrix(numpy.array(Rotation.from_quat(quaternion).as_matrix()))

        # Wrist inverse kinematics
        wrist_now_ori = [get_numpy_matrix_from_quaternion(self.get_link_orientation_quaternion(i)) for i in [4, 13]]
        wrist_target_ori = [get_numpy_matrix_from_quaternion(target_orientations[i]) for i in [0, 1]]
        wrist_action_ori = [wrist_target_ori[i] @ wrist_now_ori[i].transpose() for i in [0, 1]]
        ang = [Rotation.from_matrix(wrist_action_ori[i]).as_euler(seq='yxy', degrees=False) for i in [0, 1]]
        _angles[3:6] = [-ang for ang in ang[0]]
        _angles[9:12] = [+ang for ang in ang[1]]

        now_ang = [self.get_joint_angle(index) for index in self.available_joints_indices]
        _angles = [unwind_angles(now_ang[i], _angles[i]) for i in range(len(_angles))]

        return _angles


def get_real_target(arm_base_position: tuple, target: list[list[float]]) -> list[list[float]]:
    disp_human_demonstrate(target, [0, -0.6, 1.5 + global_z_offset])

    def cvt_target(
            target: list[list[float]], left_base_pos: list[float], right_base_pos: list[float], man_scale=2.5
    ) -> list[list[float]]:
        scale = [man_scale, man_scale]
        return (point_transfer_scale(target[:3], target[0], left_base_pos, scale=scale[0]) +
                point_transfer_scale(target[3:], target[3], right_base_pos, scale=scale[1]))

    target_points = cvt_target(target, *arm_base_position)
    pybullet.addUserDebugPoints(
        pointPositions=target_points,
        pointColorsRGB=[[1, 0, 0] for _ in range(len(target_points))],
        pointSize=2,
        lifeTime=0.1,
    )
    return target_points


def calculate_orientation_error(target_orientation_quaternion: list[list[float]],
                                now_orientation_quaternion: list[list[float]]) -> float:

    def vector_dot_loss(a, b):
        dot = (numpy.matrix(numpy.array(a)) @ numpy.matrix(numpy.array(b)).I).tolist()[0][0]
        norm = numpy.linalg.norm(numpy.array(a)) * numpy.linalg.norm(numpy.array(b))
        return 1 - dot / norm

    error = [vector_dot_loss(target, now) for target, now in zip(target_orientation_quaternion, now_orientation_quaternion)]
    return error[0] + error[1]


if __name__ == "__main__":
    demonstrate_file = h5py.File(name="./humanDemonstrate.h5", mode="r")
    simulation = UR5_Inverse_Kinematics_Simulation("./ur_description/ur5_robot_hand.urdf")
    try:
        for i in trange(len(list(demonstrate_file["l_arm"]))):
            target = demonstrate_file["l_arm"][i].tolist() + demonstrate_file["r_arm"][i].tolist()
            target = get_real_target(simulation.arm_base_position, target)
            angles = simulation.calc_inverse_kinematics(
                target_positions=target, target_orientations=demonstrate_file["ee_ori"][i].tolist(),
            )
            simulation.step_simulation(angles)
            print(calculate_orientation_error(demonstrate_file["ee_ori"][i].tolist(), simulation.ee_orientation_quaternion))
    except KeyboardInterrupt:
        pybullet.disconnect(simulation.client)
