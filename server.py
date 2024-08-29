
import struct
import cv2
import numpy
import threading
import tools
import config
import flask_cors
import flask
import json
import uuid
import time
from flask_socketio import SocketIO, emit
import pathlib

app = flask.Flask(__name__)
socket = SocketIO(app, cors_allowed_origins='*', async_mode='threading')


def codegen():
    return uuid.uuid4().hex


def default_guest_state():
    return {
        'token': '',
        'sid': '',
        'connection_status': {
            'host': {
                'width': 0,
                'height': 0,
            },
            'guest': {
                'width': 0,
                'height': 0,
            }
        }
    }


GUEST = default_guest_state()


def update_guest_state(token: str = "", sid: str = "", guest_width: int = 0, guest_height: int = 0, host_width: int = 0, host_height: int = 0):
    global GUEST
    GUEST = {
        'token': token,
        'sid': sid,
        'connection_status': {
            'host': {
                'width': host_width,
                'height': host_height,
            },
            'guest': {
                'width': guest_width,
                'height': guest_height,
            },
            "timestamp": int(time.time() * 1000)
        }
    }


frame_count = 0
last_full_frame_timestamp = time.time()
last_frame = None
compensation_ratio = 1.0
error_retry_count = 0
reference_fps = 30
reference_frame_size = 0


def emit_frame(frame: numpy.ndarray):
    global GUEST, compensation_ratio, error_retry_count, reference_frame_size
    if GUEST['sid'] != '':
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 25 if compensation_ratio == 0.0 else 50]
        compressed_frame = cv2.imencode('.jpeg', frame, encode_param)[1].tobytes()
        reference_frame_size = len(compressed_frame)
        socket.emit("frame_update_full", struct.pack("<qq", int(time.time() * 1000 - GUEST['connection_status']['timestamp']), len(compressed_frame)) + compressed_frame, namespace='/', to=GUEST['sid'])
  
def is_frame_emittable():
    global reference_fps, compensation_ratio
    target = 30
    if compensation_ratio == 1.0:
        target = 15
    else:
        target = 7
        
    if reference_fps > target:
        return frame_count % (reference_fps // target) == 0
    else:
        return True
        
def worker_thread():
    print("Worker thread started")
    
    def on_frame(screenshot: numpy.ndarray):
        while True:
            try:
                global last_frame, frame_count, last_full_frame_timestamp, error_retry_count, reference_fps, reference_frame_size
                if screenshot is None:
                    return
                if tools.get_screen_size() == (0, 0):
                    print('Update screen size', screenshot.shape[1], screenshot.shape[0])
                    tools.set_screen_size(screenshot.shape[1], screenshot.shape[0])
                if not tools.is_screen_in_rotation() and screenshot.shape[1] > screenshot.shape[0]:
                    tools.set_screen_in_rotation(True)
                elif tools.is_screen_in_rotation() and screenshot.shape[1] < screenshot.shape[0]:
                    tools.set_screen_in_rotation(False)
                
                if GUEST['sid'] != '':
                    compress_ratio = GUEST['connection_status']['host']['height'] / GUEST['connection_status']['guest']['height']
                    target_size = (int(GUEST['connection_status']['host']['width'] / compress_ratio), GUEST['connection_status']['guest']['height'])
                    
                    screenshot = tools.compress_screenshot(tools.rotate_if_horizontal(screenshot), target_size)
                    frame_count += 1
                    if time.time() - last_full_frame_timestamp > 2 or last_frame is None:
                        reference_fps = frame_count / (time.time() - last_full_frame_timestamp)
                        if last_frame is not None:
                            print("Current frame rate: ", reference_fps, "| Compensation", compensation_ratio == 0, "| Frame size", reference_frame_size)
                            frame_count = 0
                            last_full_frame_timestamp = time.time()
                        
                    if is_frame_emittable():
                        emit_frame(screenshot)
                        
                    last_frame = screenshot
                error_retry_count = 0
                return
            except Exception as e:
                error_retry_count += 1
                time.sleep(0.1)
                if error_retry_count > 10:
                    print("Error retry count exceeded. Exiting...")
                    raise e
                
    
    tools.initialize_scrcpy(on_frame)
    


@app.route('/initiate')
def initiate():
    print("Initiate request")
    global GUEST
    if flask.request.args['token'] == config.CONNECT_TOKEN:
        update_guest_state(
            token=codegen(),
        )
        return {'status': 'ok', 'token': GUEST['token']}
    else:
        return {'status': 'error', 'error': 'Invalid token'}


@socket.on('connect', namespace='/')
def connect():
    flask.session['sid'] = flask.request.sid
    print("Client entered", flask.session['sid'])


@socket.on(message="handshake", namespace='/')
def handshake(data):
    global GUEST

    print("Client request to connect", data)
    
    data = json.loads(data)
    token = ""
    try:
        token = data['token']
    except Exception as e:
        print(e)
        socket.emit("error", {"message": "Invalid form"},
                    namespace='/', to=flask.session['sid'])
        return


    if GUEST['sid'] != '':
        socket.emit("destroyed", namespace='/', to=GUEST['sid'])
        update_guest_state(token=token)
        
    # permit only if available
    if token == GUEST['token'] and GUEST['sid'] == '':
        host_resol = tools.get_screen_size()
        if host_resol[0] > host_resol[1]:
            host_resol = (host_resol[1], host_resol[0])
        try:
            guest_width = data['guest_width']
            guest_height = data['guest_height']
            
        except ValueError:
            socket.emit("error", {"message": "Invalid form"},
                        namespace='/', to=flask.session['sid'])
            return

        update_guest_state(
            token=token,
            sid=flask.session['sid'],
            guest_width=data['guest_width'],
            guest_height=data['guest_height'],
            host_width=host_resol[0],
            host_height=host_resol[1],
        )
        # print('Negotiation result', GUEST['connection_status'])
        socket.emit("connected", {"connection_status": GUEST['connection_status']},
                    namespace='/', to=flask.session['sid'])
    else:
        socket.emit("error", {"message": "Invalid second-step token."},
                    namespace='/', to=flask.session['sid'])
    


@socket.on("destroy", namespace='/')
def destory(data):
    global GUEST
    if GUEST['sid'] == flask.session['sid']:
        GUEST = default_guest_state()
        socket.emit("destroyed", namespace='/', to=flask.session['sid'])
    else:
        socket.emit("error", {"message": "No permission to destroy."},
                    namespace='/', to=flask.session['sid'])


def revert_touch_event_rotation(message: dict):
    if tools.is_screen_in_rotation():
        compress_ratio = GUEST['connection_status']['host']['height'] / GUEST['connection_status']['guest']['height']
        target_size = (int(GUEST['connection_status']['host']['width'] / compress_ratio), GUEST['connection_status']['guest']['height'])
        # swap x and y
        # print("Screen is in rotation")
        message['touch_x'], message['touch_y'] = message['touch_y'], message['touch_x']
        # message['touch_x'] = GUEST['connection_status']['guest']['height'] - message['touch_x']
        message['touch_y'] = target_size[0] - message['touch_y']
    return message


@socket.on("set_compensation_ratio", namespace='/')
def setCompensationRatio(message):
    print("Set compensation ratio", message)
    message = json.loads(message)
    global compensation_ratio
    compensation_ratio = message['ratio']


@socket.on("input_event", namespace='/')
def send(message):
    global GUEST
    message = json.loads(message)
    
    if GUEST['sid'] == flask.session['sid']:
        """
        The type of the event.
        Possible values: touch_down, touch_up, touch_move, text, btn_power, btn_back, btn_multitask, btn_home, backspace
        """
        compress_ratio = GUEST['connection_status']['host']['height'] / GUEST['connection_status']['guest']['height']
        
            
        match message['type']:
            case 'touch_down':
                message = revert_touch_event_rotation(message)
                x = int(message['touch_x'] * compress_ratio)
                y = int(message['touch_y'] * compress_ratio)
                tools.emit_touch_event(x, y, tools.scrcpy.const.ACTION_DOWN, message['touch_id'])
            case 'touch_up':
                message = revert_touch_event_rotation(message)
                x = int(message['touch_x'] * compress_ratio)
                y = int(message['touch_y'] * compress_ratio)
                tools.emit_touch_event(x, y, tools.scrcpy.const.ACTION_UP, message['touch_id'])
            case 'touch_move':
                message = revert_touch_event_rotation(message)
                x = int(message['touch_x'] * compress_ratio)
                y = int(message['touch_y'] * compress_ratio)
                tools.emit_touch_event(x, y, tools.scrcpy.const.ACTION_MOVE, message['touch_id'])
            case 'text':
                tools.emit_text_event(message['text'])
            case 'btn_power':
                tools.emit_key_event(tools.scrcpy.const.KEYCODE_POWER, tools.scrcpy.const.ACTION_DOWN)
                tools.emit_key_event(tools.scrcpy.const.KEYCODE_POWER, tools.scrcpy.const.ACTION_UP)
            case 'btn_back':
                tools.emit_key_event(tools.scrcpy.const.KEYCODE_BACK, tools.scrcpy.const.ACTION_DOWN)
                tools.emit_key_event(tools.scrcpy.const.KEYCODE_BACK, tools.scrcpy.const.ACTION_UP)
            case 'btn_multitask':
                tools.emit_key_event(tools.scrcpy.const.KEYCODE_APP_SWITCH, tools.scrcpy.const.ACTION_DOWN)
                tools.emit_key_event(tools.scrcpy.const.KEYCODE_APP_SWITCH, tools.scrcpy.const.ACTION_UP)
            case 'btn_home':
                tools.emit_key_event(tools.scrcpy.const.KEYCODE_HOME, tools.scrcpy.const.ACTION_DOWN)
                tools.emit_key_event(tools.scrcpy.const.KEYCODE_HOME, tools.scrcpy.const.ACTION_UP)
            case 'backspace':
                pass
            case _:
                pass
    else:
        socket.emit("error", {"message": "No permission to send."},
                    namespace='/', to=flask.session['sid'])


@app.route('/')
def root():
    return {'status': 'ok'}


if __name__ == '__main__':
    th = socket.start_background_task(target=worker_thread)
    socket.run(app, host='0.0.0.0', port=5013, debug=False)