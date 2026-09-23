import asyncio
import json
import logging
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
import mss
import cv2
import numpy as np
from av import VideoFrame
import pyautogui

# Disable PyAutoGUI fail-safe for remote control stability
pyautogui.FAILSAFE = False

logging.basicConfig(level=logging.INFO)

class ScreenCaptureTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self):
        super().__init__()
        self.sct = mss.mss()
        self.monitor = self.sct.monitors[1] # Primary monitor

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        sct_img = self.sct.grab(self.monitor)
        
        img = np.array(sct_img)
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        
        # Compressed to 720p to save network bandwidth over ngrok
        img_resized = cv2.resize(img, (1280, 720), interpolation=cv2.INTER_AREA)

        frame = VideoFrame.from_ndarray(img_resized, format="bgr24")
        frame.pts = pts
        frame.time_base = time_base

        await asyncio.sleep(0.03) # ~30 FPS
        return frame

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>WebRTC Live Game Stream</title>
    <style>
        body { margin: 0; background: #111; overflow: hidden; display: flex; justify-content: center; align-items: center; height: 100vh; color: white; font-family: sans-serif; }
        video { max-width: 100%; max-height: 100%; border: 2px solid #333; background: #000; cursor: crosshair; }
        #status { position: absolute; top: 10px; left: 10px; background: rgba(0,0,0,0.7); padding: 8px 12px; border-radius: 4px; font-size: 14px; }
    </style>
</head>
<body>
    <div id="status">Status: Disconnected</div>
    <video id="remoteVideo" autoplay playsinline></video>

    <script>
        const statusDiv = document.getElementById('status');
        const videoElem = document.getElementById('remoteVideo');
        
        let pc = new RTCPeerConnection({
            iceServers: [{ urls: 'stun:://google.com' }]
        });

        // Setup a Data Channel to send inputs instantly to the home PC
        let inputChannel = pc.createDataChannel("inputs");

        pc.ontrack = function(event) {
            statusDiv.innerText = "Status: Live Feed Connected";
            videoElem.srcObject = event.streams[0];
        };

        // Capture mouse clicks on the video stream
        videoElem.addEventListener('click', function(e) {
            if (inputChannel.readyState === "open") {
                const rect = videoElem.getBoundingClientRect();
                const x = (e.clientX - rect.left) / rect.width;
                const y = (e.clientY - rect.top) / rect.height;
                inputChannel.send(JSON.stringify({ type: 'click', x: x, y: y }));
            }
        });

        // Capture keyboard hits
        window.addEventListener('keydown', function(e) {
            if (inputChannel.readyState === "open") {
                inputChannel.send(JSON.stringify({ type: 'keydown', key: e.key }));
            }
        });

        async function startStream() {
            statusDiv.innerText = "Status: Initializing connection...";
            pc.addTransceiver('video', { direction: 'recvonly' });

            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);

            const response = await fetch('/offer', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    sdp: pc.localDescription.sdp,
                    type: pc.localDescription.type
                })
            });

            const answer = await response.json();
            await pc.setRemoteDescription(new RTCSessionDescription(answer));
        }

        setTimeout(startStream, 1000);
    </script>
</body>
</html>
"""

async def index(request):
    return web.Response(content_type="text/html", text=HTML_TEMPLATE)

async def offer(request):
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    
    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logging.info(f"Connection state changed to: {pc.connectionState}")
        if pc.connectionState in ["failed", "closed"]:
            await pc.close()

    # Listen for the browser's data channel to process incoming controls
    @pc.on("datachannel")
    def on_datachannel(channel):
        @channel.on("message")
        def on_message(message):
            data = json.loads(message)
            screen_width, screen_height = pyautogui.size()

            if data['type'] == 'click':
                target_x = int(data['x'] * screen_width)
                target_y = int(data['y'] * screen_height)
                pyautogui.click(target_x, target_y)

            elif data['type'] == 'keydown':
                key = data['key'].lower()
                if key == 'arrowup': key = 'up'
                elif key == 'arrowdown': key = 'down'
                elif key == 'arrowleft': key = 'left'
                elif key == 'arrowright': key = 'right'
                try:
                    pyautogui.press(key)
                except Exception:
                    pass

    video_track = ScreenCaptureTrack()
    pc.addTrack(video_track)

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.json_response({
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type
    })

async def init_app():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    return app

if __name__ == "__main__":
    app = asyncio.run(init_app())
    # Running on port 5050 to avoid Steam's port 8080
    web.run_app(app, host="0.0.0.0", port=5050)
