#!/usr/bin/env python

import rospy
from uav import uav, uav_variables
from geometry_msgs.msg import PoseStamped
from tf2_msgs.msg import TFMessage
import tf
from math import degrees
from transforms3d import _gohlketransforms,euler
import serial
import math
from sensor_msgs.msg import Range
import time
import numpy as np

# Example code for a multi-staged multi-controller wall approach, to get it close to a wall and then slowly jog in 


rate = 60 # Update rate

# For alignment of camera_frame to drone_frame(CG), in m
cameratobody_x = 0.5 # +ve is forward
cameratobody_dist = 0.5 # used for range sensor, should be same as cameratobody_x but set to a higher number to allow for less stringent deployment
contact_threshold = 0.1 # UAV is assumed to be touching the wall at this distance

# Camera Topic for desired setpoint
camera_setpoint_topic="/tf"
camera_frame_id="/camera"
uav_frame_id="/moose"
world_frame_id="/map"

# Threshold for jogging, when setpoint is under these conditions, drone will jog instead
threshold_jog=0.7 #m
threshold_jog_deg=5 #deg
# Rear Thruster Topic
thruster_output_topic="/thruster/pwm"
max_deployment_times = 1
hover_height=1.2

ser = serial.Serial('/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_58:CF:79:02:98:E4-if00', 115200) #ls /dev/serial/by-id/*

class offboard_node():

    def __init__(self):
        print("Initalising Offboard Wall Node")

        self.uav = uav() # Initalise UAV object
        self.uav.init_controller("far",0.5,0.125,0.5,0.125,0.5,0.8,0.25,0.0625) # Initalise additional controllers
        self.uav.init_controller("close",0.1,0.125,0.1,0.125,0.1,0.8,0.5,0.0625)
        aux_kp=0.2
        self.uav.init_controller("aux",aux_kp,0)
        self.camera_setpoint = uav_variables() # Initalise a set of variables to store camera setpoints

        print("Using TF Transforms for setpoints")
        self.listener = tf.TransformListener()

        self.last_acceptable_setpoint = uav_variables()
        self.last_acceptable_setpoint = self.camera_setpoint

        uav_to_body_setpoint_broadcaster = tf.TransformBroadcaster()
        
        rospy.Subscriber('Range_to_wall',Range,self.range_callback)
        self.wall_dist=999
        self.wall_timer=time.time()
        self.wall_dur=3 #s
        self.adh_timer=time.time()
        self.adh_dur=10 #s
        self.reset_timer=time.time()
        self.reset_dur=1
        self.release_stage="disarmed"

        deployment_times = 0

        self.rosrate=rospy.Rate(rate)
        rospy.on_shutdown(self.quit)
    
        while not rospy.is_shutdown():

            # Constantly try to get a transform between the uav and the camera setpoint
            # If the setpoint exists, calculate new body setpoint 0.4m out of the wall
            try:
                (trans,rot)=self.listener.lookupTransform(camera_frame_id, world_frame_id, rospy.Time(0))

                # No changes needed for rotation
                self.camera_setpoint.rx = rot[0]
                self.camera_setpoint.ry = rot[1]
                self.camera_setpoint.rz = rot[2]
                self.camera_setpoint.rw = rot[3]

                # Find vector from drone to new body setpoint by setting it cameratobody_x away from camera
                (trans_s,rot_s)=self.listener.lookupTransform(camera_frame_id, drone_frame_id, rospy.Time(0)) #to frame, from frame, time
                mag = norm(np.array(trans_s))
                trans_s_new = ( 1 - cameratobody_x/mag) * trans_s / mag

                # Send out transform again for visualisation
                local_to_base_broadcaster.sendTransform(trans_s_new, rot_s, rospy.Time.now(), uav_frame_id, "body_setpoint")

                # Look at the final transform to body_setpoint for final setpoint
                (trans_final,rot)=self.listener.lookupTransform("body_setpoint", world_frame_id, rospy.Time(0))
                self.camera_setpoint.x = trans_final[0]
                self.camera_setpoint.y = trans_final[1]
                self.camera_setpoint.z = hover_height # fix height

            except (tf.LookupException, tf.ConnectivityException, tf.ExtrapolationException):
                rospy.logdebug("Missing tf transform")

            current_yaw=euler.quat2euler([self.uav.pos.rw,self.uav.pos.rx,self.uav.pos.ry,self.uav.pos.rz])[2] #wxyz default
            setpoint_yaw=euler.quat2euler([self.camera_setpoint.rw,self.camera_setpoint.rx,self.camera_setpoint.ry,self.camera_setpoint.rz])[2] #wxyz default

            # No setpoint sent yet
            if self.camera_setpoint.x == 0 and self.camera_setpoint.y ==0 and self.camera_setpoint.z ==0:
                rospy.loginfo_throttle_identical(2, "Missing setpoint/tf, hovering at current location")
                self.uav.setpoint_quat(self.uav.pos.x,self.uav.pos.y,self.uav.pos.z,self.uav.pos.rx,self.uav.pos.ry,self.uav.pos.rz,self.uav.pos.rw) #callback local position
            elif abs(self.camera_setpoint.x - self.uav.pos.x) < threshold_jog and abs(self.camera_setpoint.y-self.uav.pos.y) < threshold_jog and abs(self.camera_setpoint.z-self.uav.pos.z) < threshold_jog:
                rospy.loginfo_throttle_identical(3,"Setpoint[%s,%s,%s] close to drone, jogging it inwards based on past position",self.last_acceptable_setpoint.x,self.last_acceptable_setpoint.y,self.last_acceptable_setpoint.z)
                 # Stop and yaw on the spot with less agressive nearfield controller when close to wall
                if degrees(abs(setpoint_yaw-current_yaw)) > threshold_jog_deg:
                    rospy.loginfo_throttle_identical(1,"Yawing towards setpoint, [%s] degrees away",degrees(abs(setpoint_yaw-current_yaw))
                    self.yaw_setpoint=uav_variables()
                    self.yaw_setpoint.x=self.uav.pos.x
                    self.yaw_setpoint.y=self.uav.pos.y
                    self.yaw_setpoint.z=hover_height
                    self.yaw_setpoint.rx=self.camera_setpoint.rx
                    self.yaw_setpoint.ry=self.camera_setpoint.ry
                    self.yaw_setpoint.rz=self.camera_setpoint.rz
                    self.yaw_setpoint.rw=self.camera_setpoint.rw
                    self.uav.setpoint_controller(self.yaw_setpoint,"close")
                    print("yaw")
               # Switch to less aggressive nearfield controller when close to wall and start translating
                elif deployment_times <max_deployment_times:
                    self.uav.setpoint_controller(self.camera_setpoint,"close")
                    
                    for i in self.uav.controller_array:
                        if i.name == "aux":
                            thr_val = i.custom_single_controller(self.wall_dist,self.wall_dist)[0]
                    
                    rospy.loginfo_throttle_identical(1,"Yaw within margin. Moving with rear thruster @ [%s]", str(translate(thr_val, 0, aux_kp, 0, 50)))
                    ser.write(str.encode(str(translate(thr_val, 0, aux_kp, 0, 100))))
                    if self.wall_dist <= contact_threshold and self.release_stage=="disarmed":
                        rospy.loginfo_throttle_identical(1,"Approached wall, stabilising")
                        self.release_stage= "contact"
                        ser.write(str.encode(self.release_stage))
                        self.wall_timer=rospy.get_time()
                    if (self.wall_dist <= contact_threshold and self.release_stage=="contact" and time.time()>=self.wall_timer+self.wall_dur):
                        rospy.loginfo_throttle_identical(1,"Touched wall and stabalised, releasing adhesive")
                        self.release_stage= "glue_release"
                        ser.write(str.encode(self.release_stage))
                        self.adh_timer=rospy.get_time()
                    if (self.wall_dist <= contact_threshold and self.release_stage=="glue_release" and time.time()>=self.adh_timer+self.adh_dur):
                        rospy.loginfo_throttle_identical(1,"Dropping payload")
                        self.release_stage="payload_drop"
                        ser.write(str.encode(self.release_stage))
                        self.reset_timer=rospy.get_time()
                    if (self.wall_dist <= contact_threshold and self.release_stage=="payload_drop" and time.time()>=self.reset_timer+self.reset_dur):
                        rospy.loginfo_throttle_identical(1,"Disarming")
                        self.release_stage="uv_off"
                        ser.write(str.encode(self.release_stage))
                        self.release_stage="payload_reset"
                        ser.write(str.encode(self.release_stage))
                        ser.write(str.encode('0')) # Set thruster to 0
                        self.release_stage="disarmed"
                        ser.write(str.encode(self.release_stage))
                        deployment_times +=1
                else:
                    rospy.loginfo_once("Deployment over")

            # Approach setpoint with aggressive controller when far 
            else:
                rospy.loginfo_throttle_identical(2,"Setpoint far from drone, using controller %s",self.uav.pos.x)
                self.last_acceptable_setpoint = self.camera_setpoint
                self.uav.setpoint_controller(self.camera_setpoint,"far")

            self.rosrate.sleep()


    def range_callback(self, msg):
        self.wall_dist = msg.range - cameratobody_dist

    
    def quit(self):
        print("Killing node")
        ser.write(str.encode('D0'))
        ser.close()
        rospy.signal_shutdown("Node shutting down")


def translate(value, leftMin, leftMax, rightMin, rightMax):
    # Figure out how 'wide' each range is
    leftSpan = leftMax - leftMin
    rightSpan = rightMax - rightMin

    # Convert the left range into a 0-1 range (float)
    valueScaled = float(value - leftMin) / float(leftSpan)

    # Convert the 0-1 range into a value in the right range.
    return rightMin + (valueScaled * rightSpan)


if __name__ == '__main__':
    
    rospy.init_node('Offboard_Wall_Node')

    node = offboard_node()

    rospy.spin()
