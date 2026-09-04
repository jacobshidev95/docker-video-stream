import asyncio
import logging
import os
from typing import Optional

from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from aiortc.mediastreams import MediaStreamTrack

ROOT = os.path.dirname(os.path.abspath(__file__))

pcs: set[RTCPeerConnection] = set()
relay = MediaRelay()
published_tracks: dict[str, MediaStreamTrack] = {}
publisher_pc: Optional[RTCPeerConnection] = None


async def index(request: web.Request) -> web.Response:
    with open(os.path.join(ROOT, "index.html"), "r", encoding="utf-8") as file:
        return web.Response(text=file.read(), content_type="text/html")


async def javascript(request: web.Request) -> web.Response:
    with open(os.path.join(ROOT, "client.js"), "r", encoding="utf-8") as file:
        return web.Response(text=file.read(), content_type="application/javascript")


async def offer(request: web.Request) -> web.Response:
    global publisher_pc

    pc: Optional[RTCPeerConnection] = None

    try:
        params = await request.json()
        role = params.get("role", "viewer")
        sdp = params.get("sdp")
        offer_type = params.get("type")

        if role not in {"publisher", "viewer"}:
            raise ValueError("role must be either 'publisher' or 'viewer'.")
        if not sdp or not offer_type:
            raise ValueError("The offer must contain 'sdp' and 'type'.")

        if role == "viewer" and not published_tracks:
            return web.json_response(
                {"error": "No browser webcam is currently publishing."},
                status=409,
            )

        if role == "publisher" and publisher_pc is not None:
            if publisher_pc.connectionState not in {"closed", "failed"}:
                return web.json_response(
                    {"error": "A webcam publisher is already connected."},
                    status=409,
                )

        pc = RTCPeerConnection()
        pcs.add(pc)

        if role == "publisher":
            publisher_pc = pc

            @pc.on("track")
            def on_track(track: MediaStreamTrack) -> None:
                if track.kind in {"audio", "video"}:
                    published_tracks[track.kind] = track
                    logging.info("Published %s track received", track.kind)

            @pc.on("connectionstatechange")
            async def on_publisher_state_change() -> None:
                logging.info("Publisher connection state: %s", pc.connectionState)
                if pc.connectionState in {"failed", "closed", "disconnected"}:
                    for track in list(published_tracks.values()):
                        track.stop()
                    published_tracks.clear()
                    if publisher_pc is pc:
                        publisher_pc = None
                    await pc.close()
                    pcs.discard(pc)

        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=sdp, type=offer_type)
        )

        if role == "viewer":
            for kind in ("audio", "video"):
                track = published_tracks.get(kind)
                if track is not None:
                    pc.addTrack(relay.subscribe(track))

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
    for track in list(published_tracks.values()):
        track.stop()
    published_tracks.clear()

    await asyncio.gather(
        *(pc.close() for pc in list(pcs)),
        return_exceptions=True,
    )
    pcs.clear()


app = web.Application()
app.on_shutdown.append(on_shutdown)
app.router.add_get("/", index)
app.router.add_get("/client.js", javascript)
app.router.add_post("/offer", offer)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    web.run_app(app, host="0.0.0.0", port=8090)