import rclpy
from rclpy.node import Node
from robo_reason_interfaces.srv import GetImage, Deproject
from sensor_msgs.msg import Image
from geometry_msgs.msg import Point

class FakeCamera(Node):
    def __init__(self):
        super().__init__('fake_camera')
        self.create_service(GetImage,  '/camera/get_image', self.get_image_cb)
        self.create_service(Deproject, '/camera/deproject',  self.deproject_cb)
        self.get_logger().info('FakeCamera ready')

    def get_image_cb(self, req, resp):
        img = Image()
        img.header.stamp = self.get_clock().now().to_msg()
        img.height = 480
        img.width  = 640
        img.encoding = 'rgb8'
        img.step = 640 * 3
        img.data = bytes(640 * 480 * 3)   # black frame
        resp.success  = True
        resp.image    = img
        resp.frame_id = 'camera_optical_frame'
        return resp

    def deproject_cb(self, req, resp):
        resp.success  = True
        resp.frame_id = 'base_link'
        resp.points   = [Point(x=0.3, y=-0.5, z=0.05) for _ in req.u]
        return resp

rclpy.init()
rclpy.spin(FakeCamera())
