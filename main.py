#!/usr/bin/env python3
import base64
import signal
import pyautogui
import cv2
import numpy as np
from fastapi import Response

from nicegui import Client, app, core, run, ui

@ui.page("/")
def home():
    ui.label("Video Stream")
    ui.label("The application is running.")

# In case you don't have a webcam, this will provide a black placeholder image.
black_1px = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAAXNSR0IArs4c6QAAAA1JREFUGFdjYGBg+A8AAQQBAHAgZQsAAAAASUVORK5CYII='
placeholder = Response(content=base64.b64decode(black_1px.encode('ascii')), media_type='image/png')

def convert(frame: np.ndarray) -> bytes:
    """Converts a frame from OpenCV to a JPEG image.

    This is a free function (not in a class or inner-function),
    to allow run.cpu_bound to pickle it and send it to a separate process.
    """
    _, imencode_image = cv2.imencode('.jpg', frame)
    return imencode_image.tobytes()


def setup() -> None:
    # OpenCV is used to access the webcam.
    video_capture = cv2.VideoCapture(0)  # 0--main web camera  1---second web camera
    if video_capture.isOpened():
        @app.get('/video/frame')
        # Thanks to FastAPI's `app.get` it is easy to create a web route which always provides the latest image from OpenCV.
        async def grab_video_frame() -> Response:
            # display width & height
            if not video_capture.isOpened():
                return placeholder
            # The `video_capture.read` call is a blocking function.
            # So we run it in a separate thread (default executor) to avoid blocking the event loop.
            frame = video_capture.read()[1]

            if frame is None:
                return placeholder
            else:
                # `convert` is a CPU-intensive function, so we run it in a separate process to avoid blocking the event loop and GIL.
                jpeg = await run.cpu_bound(convert, frame)
                return Response(content=jpeg, media_type='image/jpeg')

        @ui.page('/')
        def page():
            # For non-flickering image updates and automatic bandwidth adaptation an interactive image is much better than `ui.image()`.
            # 2. GET the current frame size
            width = video_capture.get(cv2.CAP_PROP_FRAME_WIDTH) if cv2.__version__.startswith(
                '3') else video_capture.get(
                cv2.CAP_PROP_FRAME_WIDTH)
            height = video_capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
            print(f"Camera Resolution: {int(width)}x{int(height)}")
            # get screen size
            # Returns a named tuple like Size(width=1920, height=1080)
            screen_width, screen_height = pyautogui.size()
            print(f"Screen Resolution: {int(screen_width)}x{int(screen_height)}")
            if int(width) < screen_width: # & int(height) < screen_height:
                display_width = int(width)
                display_height = int(height)
                print("int(width) < screen_width & int(height) < screen_height:")
            else:
                display_width = screen_width
                display_height = screen_height
                print("int(width) > screen_width & int(height) > screen_height:")
            print(f"Display Resolution: {int(display_width)}x{int(display_height)}")
            size_style = 'width: ' + str(display_width) + 'px; height: ' + str(display_height) + 'px'
            video_image = ui.interactive_image('/video/frame').style(size_style)

            # A timer constantly updates the source of the image.
            ui.timer(interval=0.05, callback=video_image.force_reload)

        async def disconnect() -> None:
            """Disconnect all clients from current running server."""
            for client_id in Client.instances:
                await core.sio.disconnect(client_id)

        def handle_sigint(signum, frame) -> None:
            # `disconnect` is async, so it must be called from the event loop; we use `ui.timer` to do so.
            ui.timer(0.1, disconnect, once=True)
            # Delay the default handler to allow the disconnect to complete.
            ui.timer(1, lambda: signal.default_int_handler(signum, frame), once=True)

        async def cleanup() -> None:
            # This prevents ugly stack traces when auto-reloading on code change,
            # because otherwise disconnected clients try to reconnect to the newly started server.
            await disconnect()
            # Release the webcam hardware so it can be used by other applications again.
            video_capture.release()

        # app.on_shutdown(cleanup)
        # We also need to disconnect clients when the app is stopped with Ctrl+C,
        # because otherwise they will keep requesting images which lead to unfinished subprocesses blocking the shutdown.
        signal.signal(signal.SIGINT, handle_sigint)
    else:
        print("No video capture device")
        # app.on_shutdown()


# All the setup is only done when the server starts. This avoids the webcam being accessed
# by the auto-reload main process (see https://github.com/zauberzeug/nicegui/discussions/2321).
#app.on_startup(setup)

if __name__ in {"__main__", "__mp_main__"}:
    app.on_startup(setup)
    ui.run(host='0.0.0.0', port=8090, reload=True)

