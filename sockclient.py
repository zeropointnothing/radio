import subprocess
import pyaudio
import socket
import threading
import time
import logging
import signal

logging.basicConfig(#filename="radio.log",
                    format='%(asctime)s:%(name)s/%(levelname)s: %(message)s',
                    filemode='w', datefmt='%I:%M:%S %p')

# Creating an object
logger = logging.getLogger()

# Setting the threshold of logger to DEBUG
logger.setLevel(logging.DEBUG)
logger.name = "RadioClient"

class Client:
    def __init__(self):
        self.running = True
        self.chunk_size = 90000

        self.volume = 100

        self.last_ffmpeg_chunk = 0
        self.player_timeout = 10
        
        self.ffmpeg: subprocess.Popen = None

    def heartbeat(self):
        """
        Heartbeat function to detect when FFMpeg is no longer sending data.
        """

        while self.running:
            # print(time.time() - self.last_ffmpeg_chunk)
            if time.time() - self.last_ffmpeg_chunk > self.player_timeout: # 10 seconds since FFMpeg last gave us data, probably dead.
                self.ffmpeg.terminate()
                logger.debug("Watchdog killed player!")
                break
            time.sleep(0.1)

    def start_ffmpeg(self):
        """
        Start a subprocess of FFMpeg that converts any input into 16-bit PCM (WAV).

        Send data into the process by writing to its stdin.

        Audio types are set by the first input, do NOT mix these or else
        FFMpeg will crash.
        """
        ffmpeg_cmd = [
            'ffmpeg',
            '-i', 'pipe:0',
            '-f', 's16le',  # PCM signed 16-bit little-endian
            '-ac', '2',  # 1 channel (mono)
            '-ar', '44100',  # Audio rate
            '-vn',  # No video
            'pipe:1'  # Output to stdout
        ]

        ffmpeg_process = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=10**6
        )

        self.ffmpeg = ffmpeg_process

    def play_stream(self) -> int:
        """
        Create a PyAudio instance to play audio, blocking program execution until finished.

        Automatically reads from the `ffmpeg` attribute and attempts to play it.

        Will quit upon `running` being set to False if it isn't waiting on FFMpeg.
        """
        p = pyaudio.PyAudio()
        stream = p.open(format=pyaudio.paInt16,
                        channels=2,
                        rate=44100,
                        output=True,
                        frames_per_buffer=1024)

        self.last_ffmpeg_chunk = time.time() # dont get killed immediately
        heartbeat = threading.Thread(target=self.heartbeat, daemon=True)
        heartbeat.start()

        logger.info("Player online!")

        try:
            while self.running:
                # Read the stream output from FFmpeg
                pcm_data = self.ffmpeg.stdout.read(1024)
                self.last_ffmpeg_chunk = time.time()
                # print(f"loading pcm data: {pcm_data[:10]}...")

                adjusted_data = bytearray()

                if pcm_data:
                    # adjust volume
                    for i in range(0, len(pcm_data), 2):  # Process two bytes at a time (16-bit PCM)
                        # Read two bytes (16-bit audio)
                        sample = int.from_bytes(pcm_data[i:i+2], byteorder='little', signed=True)
                        
                        # Adjust the volume by scaling the sample
                        adjusted_sample = int(sample * (self.volume/100))

                        # Clip to valid 16-bit range and append to the adjusted audio
                        adjusted_sample = max(min(adjusted_sample, 32767), -32768)
                        adjusted_data.extend(adjusted_sample.to_bytes(2, byteorder='little', signed=True))

                    stream.write(bytes(adjusted_data))
                else:
                    logger.info("FFMpeg stream down!")
                    self.running = False # player is dying, we should tell everything else to quit
                    break
        except KeyboardInterrupt:
            return 1

        stream.stop_stream()
        stream.close()
        p.terminate()
        return 0

    def fetch_audio(self):
        """
        Attempts to fetch music content from the Radio server and feed it into FFMpeg.

        Closes FFMpeg's `stdin` when the connection ends, but will leave it running regardless.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", 8000))

        sock.send(b"CONN")

        logger.info("Server connection established!")

        while True:
            data = sock.recv(self.chunk_size)
            logger.debug(f"RECIEVED {data[:10]}...")

            if not data:
                logger.info("Server stream down!")
                break

            try:
                self.ffmpeg.stdin.write(data)
                logger.debug("SEND RECV")
                sock.send(b"RECV") # ready for next chunk, send data back as a response
            except ConnectionResetError:
                logger.warning("CONNECTION DIED!")
                self.running = False
                break
            except BrokenPipeError: # FFMpeg died
                return
            except Exception as e: # other exceptions
                self.running = False
                self.ffmpeg.stdin.close()
                raise e
        self.ffmpeg.stdin.close()

cli = Client()

cli.start_ffmpeg()

thred = threading.Thread(target=cli.fetch_audio, daemon=True)
thred.start()

thred2 = threading.Thread(target=cli.play_stream, daemon=True)
thred2.start()

try:
    while cli.running:
        inp = input("> ")

        if inp == "+":
            cli.volume += 10
        elif inp == "-":
            cli.volume -= 10

        if cli.volume >= 100:
            cli.volume = 100
        elif cli.volume <= 0:
            cli.volume = 0        
except:
    pass

cli.running = False
progress = cli.ffmpeg.terminate()
threading.Thread(target=cli.ffmpeg.stdout.close, daemon=True).start() # ensure FFMepg closes without blocking execution of the client
quit_start = time.time()

while cli.ffmpeg.poll() is None:
    if time.time() - quit_start > 10:
        logger.warning("FFMpeg did not respond to SIGTERM, forcing shutdown!")
        cli.ffmpeg.send_signal(signal.SIGKILL)  # Force kill
        break
    time.sleep(0.1)

thred.join()
thred2.join()


# def play_streaming_audio(ffmpeg_cmd):
#     # Create a pyaudio stream
#     p = pyaudio.PyAudio()
#     stream = p.open(format=pyaudio.paInt16,
#                     channels=1,
#                     rate=44100,
#                     output=True)

#     # Set up FFmpeg to output PCM to stdout
#     ffmpeg_process = subprocess.Popen(
#         ffmpeg_cmd,
#         stdout=subprocess.PIPE,
#         stderr=subprocess.PIPE,
#         bufsize=10**6
#     )

#     while True:
#         # Read the stream output from FFmpeg
#         pcm_data = ffmpeg_process.stdout.read(1024)
#         if pcm_data:
#             # Convert PCM data into audio format that pyaudio can handle
#             stream.write(pcm_data)
#         else:
#             break

#     stream.stop_stream()
#     stream.close()
#     p.terminate()

# # FFmpeg command to convert MP3 to raw PCM
# ffmpeg_cmd = [
#     'ffmpeg',
#     '-i', 'test2.mp3',
#     '-f', 's16le',  # PCM signed 16-bit little-endian
#     '-ac', '1',  # 1 channel (mono)
#     '-ar', '44100',  # Audio rate
#     '-vn',  # No video
#     'pipe:1'  # Output to stdout
# ]

# # Start playing the audio
# play_streaming_audio(ffmpeg_cmd)
