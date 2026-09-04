import argparse
import asyncio
import logging
import os
import platform
import ssl
from typing import Optional

from aiohttp import web
from aiortc import (
    MediaStreamTrack,
    RTCPeerConnection,
    RTCRtpSender,
    RTCSessionDescription,
)
from aiortc.contrib.media import MediaPlayer, MediaRelay

ROOT = os.path.dirname(os.path.abspath(__file__))

pcs: set[RTCPeerConnection] = set()
relay: Optional[MediaRelay] = None
webcam: Optional[MediaPlayer] = None


def create_local_tracks(
    play_from: Optional[str], decode: bool
) -> tuple[Optional[MediaStreamTrack], Optional[MediaStreamTrack]]:
    """Create the audio/video source used by the WebRTC connection."""
    global relay, webcam

    if play_from:
        if not os.path.exists(play_from):
            raise FileNotFoundError(
                f"Media source does not exist: {play_from}. "
                "Use a valid file path mounted inside the container."
            )

        player = MediaPlayer(play_from, decode=decode)
        return player.audio, player.video

    # A cloud VPS normally does not have a physical webcam.
    options = {"framerate": "30", "video_size": "640x480"}

    if platform.system() == "Darwin":
        camera_source = "default:none"
        camera_format = "avfoundation"
    elif platform.system() == "Windows":
        camera_source = "video=Integrated Camera"
        camera_format = "dshow"
    else:
        camera_source = "/dev/video0"
        camera_format = "v4l2"

        if not os.path.exists(camera_source):
            raise RuntimeError(
                "No media source configured. The VPS has no /dev/video0 webcam. "
                "Start the application with --play-from /app/<video-file>."
            )

    if relay is None:
        webcam = MediaPlayer(
            camera_source,
            format=camera_format,
            options=options,
        )
        relay = MediaRelay()

    if webcam is None or webcam.video is None:
        raise RuntimeError("The camera source did not provide a video track.")

    return None, relay.subscribe(webcam.video)


def force_codec(
    pc: RTCPeerConnection, sender: RTCRtpSender, forced_codec: str
) -> None:
    kind = forced_codec.split("/")[0]
    codecs = RTCRtpSender.getCapabilities(kind).codecs
    transceiver = next(t for t in pc.getTransceivers() if t.sender == sender)
    transceiver.setCodecPreferences(
        [codec for codec in codecs if codec.mimeType == forced_codec]
    )


async def index(request: web.Request) -> web.Response:
    with open(os.path.join(ROOT, "index.html"), "r", encoding="utf-8") as file:
        content = file.read()
    return web.Response(content_type="text/html", text=content)


async def javascript(request: web.Request) -> web.Response:
    with open(os.path.join(ROOT, "client.js"), "r", encoding="utf-8") as file:
        content = file.read()
    return web.Response(content_type="application/javascript", text=content)


async def offer(request: web.Request) -> web.Response:
    """Accept a browser WebRTC offer and return exactly one JSON answer."""
    pc: Optional[RTCPeerConnection] = None

    try:
        params = await request.json()

        if not isinstance(params, dict):
            raise ValueError("The request body must be a JSON object.")

        if not params.get("sdp") or not params.get("type"):
            raise ValueError("The offer must contain both 'sdp' and 'type'.")

        remote_offer = RTCSessionDescription(
            sdp=params["sdp"],
            type=params["type"],
        )

        pc = RTCPeerConnection()
        pcs.add(pc)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            logging.info("Connection state is %s", pc.connectionState)
            if pc.connectionState == "failed":
                await pc.close()
                pcs.discard(pc)

        audio, video = create_local_tracks(
            args.play_from,
            decode=not args.play_without_decoding,
        )

        if audio:
            audio_sender = pc.addTrack(audio)
            if args.audio_codec:
                force_codec(pc, audio_sender, args.audio_codec)
            elif args.play_without_decoding:
                raise ValueError(
                    "Specify --audio-codec when using --play-without-decoding."
                )

        if video:
            video_sender = pc.addTrack(video)
            if args.video_codec:
                force_codec(pc, video_sender, args.video_codec)
            elif args.play_without_decoding:
                raise ValueError(
                    "Specify --video-codec when using --play-without-decoding."
                )

        await pc.setRemoteDescription(remote_offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        return web.json_response(
            {
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type,
            }
        )

    except (ValueError, TypeError) as error:
        if pc is not None:
            await pc.close()
            pcs.discard(pc)
        return web.json_response({"error": str(error)}, status=400)

    except Exception as error:
        logging.exception("WebRTC offer failed")
        if pc is not None:
            await pc.close()
            pcs.discard(pc)
        return web.json_response({"error": str(error)}, status=500)


async def on_shutdown(app: web.Application) -> None:
    await asyncio.gather(*(pc.close() for pc in pcs), return_exceptions=True)
    pcs.clear()

    if webcam is not None and webcam.video is not None:
        webcam.video.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WebRTC video stream server")
    parser.add_argument("--cert-file", help="SSL certificate file (for HTTPS)")
    parser.add_argument("--key-file", help="SSL key file (for HTTPS)")
    parser.add_argument(
        "--play-from",
        help="Read audio/video from a file or media URL available inside the container.",
    )
    parser.add_argument(
        "--play-without-decoding",
        help=(
            "Read the media without decoding it. "
            "This currently requires an MPEG-TS source with H.264 video."
        ),
        action="store_true",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host for the HTTP server (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8090,
        help="Port for the HTTP server (default: 8090)",
    )
    parser.add_argument("--verbose", "-v", action="count")
    parser.add_argument(
        "--audio-codec",
        help="Force a specific audio codec, for example audio/opus.",
    )
    parser.add_argument(
        "--video-codec",
        help="Force a specific video codec, for example video/H264.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.cert_file:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(args.cert_file, args.key_file)
    else:
        ssl_context = None

    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_get("/client.js", javascript)
    app.router.add_post("/offer", offer)

    web.run_app(
        app,
        host=args.host,
        port=args.port,
        ssl_context=ssl_context,
    )