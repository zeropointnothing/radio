import subprocess
import pyaudio
import socket
import threading
import time
import logging
import signal
import json
import sys
import curses

logging.basicConfig(filename="radioclient.log",
                    format='%(asctime)s:%(name)s/%(levelname)s: %(message)s',
                    filemode='w', datefmt='%I:%M:%S %p')

# Creating an object
logger = logging.getLogger()

# Setting the threshold of logger to DEBUG
logger.setLevel(logging.INFO)
logger.name = "RadioClient"

class Client:
    def __init__(self):
        self.running = True
        self.chunk_size = 90000

        self.volume = 100

        self.host = ""

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
        host, port = self.host.split(":", maxsplit=1)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, int(port)))

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

class Application:
    def __init__(self, client: Client):
        self.client = client

        self.stdscr: curses.window = None
        self.h, self.w = 0,0

        self.fetch_thread = threading.Thread(target=self.client.fetch_audio, daemon=True)
        self.play_thread = threading.Thread(target=self.client.play_stream, daemon=True)


    def get_status(self, url: str) -> dict:
        host, port = url.split(":", maxsplit=1)
        
        # display what we're joining
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, int(port)))

            sock.send(b"STAT") # ask for the status
            resp = sock.recv(4) # first 4 bytes will always either by NFND or the length of a successful response.
        except Exception as e:
            # return False
            raise e
            print("Unable to establish a connection with the Radio server.")
            print("Contact the Radio owner about this issue. It's likely they don't have the server running, or its outdated.")
            print(f"error: {e}")
            sys.exit(1)

        if resp == "NFND":
            raise ConnectionError("Radio server experiencing issues.")
            print("The Radio server is experiencing errors and the connection should not proceed.")
            print("Contact the Radio owner about this issue, as they most likely have a misconfigured playlist.")
            sys.exit(1)
        else:
            resp_length = int(resp)
            resp = sock.recv(resp_length)

            status = json.loads(resp)
            return status

            print("= Radio Status (Currently Playing) =")
            print(f"Uptime: {status["uptime"]} sec / {status["radio_time"]}")
            print(f"{status["current"]["author"]} - {status["current"]["title"]}")

            user = input("\nConnect? (Y/n): ")
            if not user.lower() == "y":
                sys.exit()

    def start_radio(self, host: str):
        self.client.start_ffmpeg()
        self.fetch_thread.start()
        self.play_thread.start()

        start = 0

        try:
            while client.running:
                k = self.stdscr.getch()
                self.h, self.w = self.stdscr.getmaxyx()

                if time.time() - start > 5:
                    start = time.time()
                    status = self.get_status(host)
                    self.stdscr.clear()

                title = f"~ Connected to Radio ~"
                uptime = f"Radio Time: {status["radio_time"]}"
                track = f"{status["current"]["author"]} - {status["current"]["title"]}"
                volume = f"{self.client.volume}%"
                instruct = "Volume: +/-, Quit: q"

                self.stdscr.move((self.h//2)-3, 0)
                self.stdscr.clrtoeol()
                self.stdscr.addstr((self.h//2)-3, (self.w//2)-(len(title)//2), title)
                self.stdscr.clrtoeol()

                self.stdscr.move((self.h//2)-2, 0)
                self.stdscr.clrtoeol()
                self.stdscr.addstr((self.h//2)-2, (self.w//2)-(len(uptime)//2), uptime)
                self.stdscr.clrtoeol()
                
                self.stdscr.move((self.h//2)-1, 0)
                self.stdscr.clrtoeol()
                self.stdscr.addstr((self.h//2)-1, (self.w//2)-(len(track)//2), track)
                self.stdscr.clrtoeol()

                self.stdscr.move((self.h//2), 0)
                self.stdscr.clrtoeol()
                self.stdscr.addstr((self.h//2), (self.w//2)-(len(volume)//2), volume)
                self.stdscr.clrtoeol()

                self.stdscr.move((self.h//2)+1, 0)
                self.stdscr.clrtoeol()
                self.stdscr.addstr((self.h//2)+1, (self.w//2)-(len(instruct)//2), instruct)
                self.stdscr.clrtoeol()


                if k == -1:
                    continue
                elif chr(k) == "+":
                    self.client.volume += 10
                elif chr(k) == "-":
                    self.client.volume -= 10
                elif chr(k) == "q":
                    break

                if self.client.volume >= 100:
                    self.client.volume = 100
                elif self.client.volume <= 0:
                    self.client.volume = 0

                time.sleep(0.005)
        except:
            pass

        self.client.running = False
        self.client.ffmpeg.terminate()
        threading.Thread(target=self.client.ffmpeg.stdout.close, daemon=True).start() # ensure FFMepg closes without blocking execution of the client
        quit_start = time.time()

        while self.client.ffmpeg.poll() is None:
            if time.time() - quit_start > 10:
                logger.warning("FFMpeg did not respond to SIGTERM, forcing shutdown!")
                self.client.ffmpeg.send_signal(signal.SIGKILL)  # Force kill
                break
            time.sleep(0.1)

        self.fetch_thread.join()
        self.play_thread.join()
        self.stdscr.clear()

    def main(self, stdscr: curses.window):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)
        curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_WHITE)

        color_red_black = curses.color_pair(1)
        color_black_white = curses.color_pair(2)
        color_white_black = curses.color_pair(3)
        color_yellow_white = curses.color_pair(4)

        self.stdscr = stdscr

        self.stdscr.nodelay(True)
        self.stdscr.keypad(True)
        user_input = ""

        self.h, self.w = self.stdscr.getmaxyx()

        while True:
            k = self.stdscr.getch()
            self.h, self.w = self.stdscr.getmaxyx()

            instruct = "~ Enter Radio URL/Port ~"


            if k == -1:
                pass
            elif chr(k) in "abcdefghijklmnopqrstuvwxyz1234567890.: ":
                user_input = user_input + chr(k)
            elif k in [curses.KEY_BACKSPACE, 127, 8]:
                user_input = user_input[:len(user_input)-1]
            elif k in [curses.KEY_ENTER, 10, 13] and user_input:
                instruct = f"Attempting to connect to '{user_input}'..."
                self.stdscr.addstr((self.h//2)-2, (self.w//2)-(len(instruct)//2), instruct)
                self.stdscr.refresh()
                time.sleep(0.5)
                status = self.get_status(user_input)

                self.stdscr.clear()

                while True:
                    title = "= Radio Status (Currently Playing) ="
                    uptime = f"Uptime: {status["uptime"]} sec / {status["radio_time"]}"
                    track = f"{status["current"]["author"]} - {status["current"]["title"]}"
                    instruct = "Connect? (Y/n)"

                    self.stdscr.addstr((self.h//2)-3, (self.w//2)-(len(title)//2), title)
                    self.stdscr.addstr((self.h//2)-2, (self.w//2)-(len(uptime)//2), uptime)
                    self.stdscr.addstr((self.h//2)-1, (self.w//2)-(len(track)//2), track)
                    self.stdscr.addstr((self.h//2)+1, (self.w//2)-(len(instruct)//2), instruct)
                    self.stdscr.refresh()

                    k = self.stdscr.getch()

                    if k == -1:
                        continue
                    elif chr(k).lower() == "y":
                        self.stdscr.clear()
                        self.client.host = user_input
                        self.start_radio(user_input)
                        break
                    elif chr(k).lower() == "n":
                        self.stdscr.clear()
                        break

                    time.sleep(0.05)
                continue


            user_input_str = user_input+f" "*(14-len(user_input))

            self.stdscr.move((self.h//2)-2, 0)
            self.stdscr.clrtoeol()
            self.stdscr.addstr((self.h//2)-2, (self.w//2)-(len(instruct)//2), instruct)
            self.stdscr.clrtoeol()

            self.stdscr.move((self.h//2)-1, 0)
            self.stdscr.clrtoeol()
            if user_input:
                self.stdscr.addstr((self.h//2)-1, (self.w//2)-(len(user_input_str)//2), user_input_str, color_black_white)
            else:
                placeholder = "127.0.0.1:8000"
                self.stdscr.addstr((self.h//2)-1, (self.w//2)-(len(placeholder)//2), placeholder, color_yellow_white)
            self.stdscr.clrtoeol()

            self.stdscr.refresh()
            time.sleep(0.005)


client = Client()
app = Application(client=client)

curses.wrapper(app.main)
