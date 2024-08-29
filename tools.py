import numba
import os
import cv2
import numpy
import ppadb
import ppadb.client
import ppadb.device
import scrcpy
from typing import Optional

import config

client = ppadb.client.Client()
scrcpyCli: Optional[scrcpy.Client] = None

def get_device() -> ppadb.device.Device:
    """
    Get the first device connected to the PC.
    
    Returns:
        ppadb.device.Device: The first device connected to the PC.
    """
    return client.devices()[0]

screen_size = (0, 0)
screen_in_rotation = False

def set_screen_size(width: int, height: int) -> None:
    global screen_size
    screen_size = (width, height)


def is_screen_in_rotation() -> bool:
    global screen_in_rotation
    return screen_in_rotation

def set_screen_in_rotation(in_rotation: bool) -> None:
    global screen_in_rotation
    screen_in_rotation = in_rotation


def rotate_if_horizontal(img: numpy.ndarray) -> numpy.ndarray:
    if screen_in_rotation:
        img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    
    return img


def compress_screenshot(img: numpy.ndarray, target_size: tuple) -> numpy.ndarray:
    """
    Compress the screenshot to the target size.
    
    Args:
        img (numpy.ndarray): The screenshot to be compressed.
        target_size (tuple): The target size of the compressed screenshot.
    
    Returns:
        numpy.ndarray: The compressed screenshot.
    """
    return cv2.resize(img, target_size, interpolation=cv2.INTER_AREA)
    
    

def calculate_binary_diff(img1: numpy.ndarray, img2: numpy.ndarray) -> bytes:
    """
    Calculate the binary difference between two images.

    Args:
        img1 (numpy.ndarray): The first image.
        img2 (numpy.ndarray): The second image.

    Returns:
        bytes: The binary difference between the two images.
    """

    img = img2 - img1
    # print(img)
    return cv2.imencode('.jpeg', img)[1].tobytes()
    


def get_screen_size() -> tuple:
    return screen_size
    
    
    
def initialize_scrcpy(on_frame_cb) -> None:
    import adbutils
    
    print(adbutils.adb.device_list())
    
    global scrcpyCli
    scrcpyCli = scrcpy.Client(max_fps=30, stay_awake=True, encoder_name=config.ENCODER, codec_name=config.CODEC)
    # screen on if not already on
    if not scrcpyCli.device.is_screen_on():
        scrcpyCli.control.keycode(26, scrcpy.const.ACTION_DOWN)
        scrcpyCli.control.keycode(26, scrcpy.const.ACTION_UP)
    
    print("Initializing Scrcpy...")
    scrcpyCli.add_listener(scrcpy.EVENT_FRAME, on_frame_cb)
    
    def on_init():
        
        print(scrcpyCli.device_name, scrcpyCli.encoder_name, get_screen_size())
        
    scrcpyCli.add_listener(scrcpy.EVENT_INIT, on_init)
    scrcpyCli.start()
    
def emit_touch_event(x, y, action, touch_id=0):
    scrcpyCli.control.touch(x, y, action, touch_id)
    
def emit_key_event(key_code, action):
    scrcpyCli.control.keycode(key_code, action)
    
def emit_text_event(text):
    print("Emiiting text event: " + str(text.encode('utf-8')))
    scrcpyCli.control.text(text)