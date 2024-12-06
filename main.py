import uuid, time, os, signal, json, itertools
import asyncio, uvicorn
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi import WebSocket
from threading import Thread, Lock, Event
from collections import deque

logger = logging.getLogger('uvicorn')

# @app.get("/")
# def read_root():
#     return FileResponse("test.html")

class Radio:
    def __init__(self):
        """
        Radio.
        """
        # Circular buffer for audio chunks
        self.buffer_lock = Lock()
        self.shutdown = False
        self.bitrate = 0
        
        with open("authkey.txt", "r") as f:
            self.key = f.read()

        self.active_connections = 0
        self.wake_consumers = asyncio.Condition()

        self.playlist = []
        self.buffer = []
        # Flag to control the producer thread
        self.streaming_active = True
        self.up_time = 0

    async def stop(self):
        """
        Shutdown the Radio.

        Wakes up all consumers (who are waiting) and informs them to shutdown.
        """
        self.shutdown = True

        async with self.wake_consumers:
            self.wake_consumers.notify_all()

    async def add_track(self, title: str, author: str, path: str):
        """
        Add a track to the buffer.
        """
        track = {"meta": {"title":title,"author":author,"path":path}, "data": []}
        self.buffer.append(track)

        track = self.buffer.index(track)

        with open(path, "rb") as audio_file:
            chunk = audio_file.read(50000)  # Read 50KB chunks
            while chunk and self.streaming_active:
                with self.buffer_lock:
                    self.buffer[track]["data"].append(chunk)
                chunk = audio_file.read(50000)
                time.sleep(0.01)  # Simulate streaming rate
        async with self.wake_consumers:
            self.wake_consumers.notify_all()

    def get_chunk_from_time(self, bitrate_kbps: int) -> int:
        """
        Fetch the chunk number based on the time (in seconds).

        `bitrate_kbps` should be in KB/s.

        Note that if `bitrate_kbps` is not correct, this will be inaccurate.
        """
        # Convert bitrate to bytes per second
        bytes_per_second = (bitrate_kbps * 1000) // 8  # kbps to Bps

        # Calculate total bytes for the given time
        total_bytes = self.up_time * bytes_per_second

        # Find the position in the list
        chunk_index = int(total_bytes // 50000)
        return chunk_index

    def get_track_from_chunk(self, chunk: int) -> dict | None:
        """
        Fetch track information from the chunk number.
        """
        chunks = self.get_all_chunks()
        chunk = chunks[chunk]

        for track in self.buffer:
            try:
                if track["data"].index(chunk):
                    return track["meta"]
            except ValueError:
                continue

        return None

    def get_all_chunks(self):
        return list(itertools.chain(*[_["data"] for _ in self.buffer]))

    def producer(self):
        """
        'Produces' radio playlist.

        Loads all tracks in `playlist`, then begins incrementing `up_time` by one every second.
        """
        # for i, track in enumerate(self.playlist):
        #     logger.info(f"BEGIN LOAD: {track["title"]}...")
        #     self.buffer.append({"meta": track, "data": []})
        #     try:
        #         with open(track["path"], "rb") as audio_file:
        #             chunk = audio_file.read(50000)  # Read 50KB chunks
        #             while chunk and self.streaming_active:
        #                 with self.buffer_lock:
        #                     self.buffer[i]["data"].append(chunk)
        #                 chunk = audio_file.read(50000)
        #                 time.sleep(0.01)  # Simulate streaming rate
        #     except FileNotFoundError:
        #         print(f"File not found: {track}")
        #     except Exception as e:
        #         print(f"Error processing file {track}: {e}")

        # Handle silence or looping if needed
        while self.streaming_active:
            self.up_time += 1
            time.sleep(1)

    async def consumer(self, request: Request):
        """
        'Consumes' radio playlist.
        
        Generator that yields chunks from `producer`'s `buffer`.
        To shutdown all consumers, call `stop`.
        """
        if self.shutdown:
            return

        id = uuid.uuid4()
        self.active_connections += 1
        logger.info(f"Stream Connection ({id})! Current streams now at: {self.active_connections}")

        start_index = self.get_chunk_from_time(self.bitrate)
        current_index = start_index

        while not self.shutdown:
            if await request.is_disconnected():
                self.active_connections -= 1
                logger.info(f"Stream {id} going down! (Reason: Client Disconnect)")
                return

            # Stream the chunk if available
            if current_index < len(self.get_all_chunks()):
                chunk = self.get_all_chunks()[current_index]
                logger.info(f"YIELDING: {chunk[:2]}")
                current_index += 1
                yield chunk

            # Wait for data or shutdown signal
            async with self.wake_consumers:
                while not self.shutdown and current_index >= len(self.get_all_chunks()):
                    try:
                        await asyncio.wait_for(self.wake_consumers.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        break

                if self.shutdown:
                    break

        self.active_connections -= 1
        logger.info(f"Stream {id} going down! (Reason: Shutdown / Remaining Streams: {self.active_connections})")


radio = Radio()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # start code
    logger.info("Initializing playlist...")

    with open("tracks.json", "r") as f:
        data = json.load(f)
        radio.playlist = data["tracks"]
        radio.bitrate = data["bitrate"]

    for track in radio.playlist:
        logger.info(f"BEGIN LOAD: {track["title"]}...")
        radio.buffer.append({"meta": track, "data": []})
        try:
            await radio.add_track(track["title"], track["author"], track["path"])
        except FileNotFoundError:
            logger.error(f"File not found: {track["path"]}")
        except Exception as e:
            logger.error(f"Error processing file {track["path"]}: {e}")

    producer_thread = Thread(target=radio.producer, daemon=True)
    producer_thread.start()

    # chunk_time_thread = Thread(target=radio.get_chunk_time, daemon=True)
    # chunk_time_thread.start()
    
    try:
        yield
    finally:
        # shutdown

        radio.shutdown = True
        radio.streaming_active = False
        if not radio.shutdown:
            radio.stop()
        with radio.buffer_lock:
            radio.buffer.clear()  # Clear the buffer to unblock any waiting consumers
        logger.info("Waiting for producer to go down...")
        producer_thread.join(80)


app = FastAPI(lifespan=lifespan)

app.mount("/web", StaticFiles(directory="static"), name="web")


@app.get("/stream")
async def radio_stream(request: Request):
    """
    Main radio endpoint.

    Creates a `consumer` that will continue serving audio to the client for as long as the connection is open.
    """
    if radio.shutdown:
        return HTMLResponse("Radio is still shutting down!", 503)

    response = StreamingResponse(
        radio.consumer(request), 
        media_type="audio/mpeg"
    )

    # Set headers for live streaming
    response.headers["Content-Type"] = "audio/mpeg"
    response.headers["Accept-Ranges"] = "none"  # Disallow seeking
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"  # Ensure compatibility with older clients
    response.headers["Expires"] = "0"  # Indicate content expires immediately
    response.headers["Transfer-Encoding"] = "chunked"  # Indicate live stream

    return response

@app.post("/add")
async def add_to_playlist(title: str, author: str, path: str):
    """Dynamically add a file to the playlist."""
    # with radio.buffer_lock:
    #     radio.playlist.append(file)
    logger.info(f"Remote load request recieved ({path}).")
    try:
        await radio.add_track(title, author, path)
        logging.info("Remote request was successful.")
        return HTMLResponse(f"File '{path}' added to playlist", 200)
    except FileNotFoundError:
        logger.error(f"No such file '{path}'")
        return HTMLResponse(f"No such file '{path}'.")

@app.get("/current")
def get_current():
    current_chunk = radio.get_chunk_from_time(radio.bitrate)
    return {"meta": radio.get_track_from_chunk(current_chunk), "time": radio.up_time}

@app.get("/shutdown")
async def shutdown(key: str):
    if key == radio.key:
        logger.warning("Force shutdown request recieved! Aborting all streams!")
        logger.warning("Goodbye! (attempting to send SIGINT to API...)")
        os.kill(os.getpid(), signal.SIGINT)
        await radio.stop()
        return HTMLResponse("Server shutdown acknowledged.", 200)
    else:
        return HTMLResponse("Request denied. Unauthorized.", 401)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        await asyncio.sleep(0.1)
        payload = next("audio")
        await websocket.send_json(payload)


if __name__ == "__main__":
    uvicorn.run(app, port=8080, host="0.0.0.0")
