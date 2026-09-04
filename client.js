const startButton = document.getElementById("start");
const stopButton = document.getElementById("stop");
const modeSelect = document.getElementById("mode");
const localVideo = document.getElementById("localVideo");
const remoteVideo = document.getElementById("remoteVideo");
const statusText = document.getElementById("status");

let peerConnection = null;
let localStream = null;

function setStatus(message) {
    if (statusText) {
        statusText.textContent = message;
    }
}

function selectedMode() {
    return modeSelect ? modeSelect.value : "viewer";
}

async function negotiate(role) {
    const offer = await peerConnection.createOffer();
    await peerConnection.setLocalDescription(offer);

    await new Promise((resolve) => {
        if (peerConnection.iceGatheringState === "complete") {
            resolve();
            return;
        }

        const checkState = () => {
            if (peerConnection.iceGatheringState === "complete") {
                peerConnection.removeEventListener(
                    "icegatheringstatechange",
                    checkState
                );
                resolve();
            }
        };

        peerConnection.addEventListener(
            "icegatheringstatechange",
            checkState
        );
    });

    const response = await fetch("/offer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            role,
            sdp: peerConnection.localDescription.sdp,
            type: peerConnection.localDescription.type,
        }),
    });

    const body = await response.json();

    if (!response.ok) {
        throw new Error(body.error || `Offer failed with HTTP ${response.status}`);
    }

    await peerConnection.setRemoteDescription(body);
}

async function startPublisher() {
    setStatus("Requesting camera and microphone permission...");

    localStream = await navigator.mediaDevices.getUserMedia({
        video: true,
        audio: true,
    });

    if (localVideo) {
        localVideo.srcObject = localStream;
        localVideo.muted = true;
        await localVideo.play();
    }

    peerConnection = new RTCPeerConnection();

    for (const track of localStream.getTracks()) {
        peerConnection.addTrack(track, localStream);
    }

    peerConnection.onconnectionstatechange = () => {
        setStatus(`Publisher connection: ${peerConnection.connectionState}`);
    };

    setStatus("Connecting webcam to the server...");
    await negotiate("publisher");
    setStatus("Webcam is live. Other browsers can now view it.");
}

async function startViewer() {
    peerConnection = new RTCPeerConnection();

    peerConnection.addTransceiver("video", { direction: "recvonly" });
    peerConnection.addTransceiver("audio", { direction: "recvonly" });

    peerConnection.ontrack = (event) => {
        if (remoteVideo && event.streams[0]) {
            remoteVideo.srcObject = event.streams[0];
            remoteVideo.play().catch(() => {
                setStatus("Click the video to allow playback.");
            });
        }
    };

    peerConnection.onconnectionstatechange = () => {
        setStatus(`Viewer connection: ${peerConnection.connectionState}`);
    };

    setStatus("Connecting to the live webcam...");
    await negotiate("viewer");
    setStatus("Live stream connected.");
}

async function start() {
    stop();

    try {
        if (!navigator.mediaDevices) {
            throw new Error("Camera access requires HTTPS or localhost.");
        }

        if (selectedMode() === "publisher") {
            await startPublisher();
        } else {
            await startViewer();
        }
    } catch (error) {
        console.error(error);
        setStatus(`Error: ${error.message}`);
        stop();
    }
}

function stop() {
    if (localStream) {
        for (const track of localStream.getTracks()) {
            track.stop();
        }
        localStream = null;
    }

    if (peerConnection) {
        peerConnection.close();
        peerConnection = null;
    }

    if (localVideo) {
        localVideo.srcObject = null;
    }
}

if (startButton) {
    startButton.addEventListener("click", start);
}

if (stopButton) {
    stopButton.addEventListener("click", () => {
        stop();
        setStatus("Stopped.");
    });
}