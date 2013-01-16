#!/usr/bin/env python

import roslib;
roslib.load_manifest('flyvr')
roslib.load_manifest('std_msgs')
import rospy

from std_msgs.msg import String, UInt32

import random
import json
import pickle
import threading
import subprocess
import fnmatch
import argparse
import os.path

import numpy as np
import scipy.misc
import cv,cv2

import calib

#colors to draw on the image for each point type, and their value.
COLORS = {
    calib.POINT_TYPE_MANUAL:[(0,255)],
    calib.POINT_TYPE_LASER:[(1,255)],
    calib.POINT_TYPE_PROJECTOR:[(2,255)],
}

class Foo:
    def __init__(self, cameras_glob, only_detected):
        self.results = {}
        self.resolution = {}
        self.windows = {}
        self.cameras_glob = cameras_glob
        self.lock = threading.Lock()
        self.process = None
        self.only_detected = only_detected
        cv2.startWindowThread()

    def load_from_pickle(self, base='mcamall'):
        with open('%s/results.pkl' % base) as f:
            self.results = pickle.load(f)
        with open('%s/results_mode.pkl' % base) as f:
            self.results_mode = pickle.load(f)
        with open('%s/resolution.pkl' % base) as f:
            self.resolution = pickle.load(f)

        self._update_windows()

    def from_ros(self):
        rospy.Subscriber("/multicamselfcal_everything/points", String, self._on_points)
        rospy.Subscriber("/multicamselfcal_everything/num_points", UInt32, self._on_num_points)
        
    def _speak(self, msg):
        if self.process != None:
            self.process.poll()
            if self.process.returncode == None:
                return
        self.process = subprocess.Popen('espeak "%s" --stdout | aplay' % msg, shell=True)

        
    def _on_num_points(self, data):
        self._speak(data.data)

    def _on_points(self, data):
        with self.lock:
            dat = json.loads(data.data)
            self.results = dat['results']
            self.results_mode = dat['results_mode']
            self.resolution = dat['resolution']
        self._update_windows()

    def _update_windows(self):
        if self.results and self.resolution and (len(self.results) == len(self.resolution)):
            allcams = self.resolution.keys()
            for handle in fnmatch.filter(allcams, self.cameras_glob):
                arr,npts = self._make_array(handle)
                if handle not in self.windows and (npts > 0 or self.only_detected == False):
                    cv2.namedWindow(handle, cv.CV_WINDOW_NORMAL)
                    self.windows[handle] = True
                if handle in self.windows:
                    cv2.putText(arr, "%s (%d)" % (handle,npts), (100,100), cv2.FONT_HERSHEY_PLAIN, 1.0, (255, 255, 255))
                    cv2.imshow(handle, arr)

    def _make_array(self, cam):
        w,h = self.resolution[cam]
        #swap h,w as resolution saves in image coord convention and scipy.misc.imsave saves
        #from matrix convention
        npts = 0
        #in opencv convention, bgra
        arr = np.zeros((h,w,4),dtype=np.uint8)
        arr[:,:,3]=255 #opaque
        for i,pt in enumerate(self.results[cam]):
            #again swap image coords -> matrix notation
            col,row = pt
            if np.any(np.isnan(pt)):
                continue
            for (colorchan,colorval) in COLORS[self.results_mode[cam][i]]:
                arr[row,col,colorchan] = colorval
            npts += 1
        return arr, npts

    def write_pngs(self):
        for cam in self.results:
            arr,npts = self._make_array(cam)
            scipy.misc.imsave('coverage-%s.png' % cam.replace('/',''),arr)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--cameras', type=str, default='*',
        help='glob pattern of cameras to show, e.g. /Basler*')
    parser.add_argument(
        '--from-dir', type=str,
        help='load from dir of pkl files')
    parser.add_argument(
        '--only-detected', action='store_true', default=False, help=\
        'only show cameras with detected points')
    argv = rospy.myargv()
    args = parser.parse_args(argv[1:])

    rospy.init_node('viewcalibpoints', anonymous=True)
    r = Foo(args.cameras, args.only_detected)
    if args.from_dir:
        r.load_from_pickle(os.path.abspath(args.from_dir))
    else:
        r.from_ros()
    rospy.spin()
    
