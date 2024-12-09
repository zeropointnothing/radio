import socket
import time
import threading
import logging
import uuid
import os
from collections import deque

logging.basicConfig(#filename="radio.log",
                    format='%(asctime)s:%(name)s/%(levelname)s: %(message)s',
                    filemode='w', datefmt='%I:%M:%S %p')

# Creating an object
logger = logging.getLogger()

# Setting the threshold of logger to DEBUG
logger.setLevel(logging.DEBUG)
logger.name = "RadioServer"

class Watchdog:
    def __init__(self):
        """
        Watchdog class.

        Offers a convientient way to watch for processes that do not shut down properly.
        """
        self.death = 100
        self.running = False

        self.__proccesses = []
        self.__mod_lock = threading.Lock()

    @property
    def active(self):
        """
        The amount of processes the Watchdog is aware of.
        """
        return len(self.__proccesses)

    def beat(self, id: str) -> None:
        """
        Process heartbeat. Should be called every iteration to prove the thread is
        still alive.
        """

        proc = [_ for _ in self.__proccesses if _["id"] == id][0]

        proc["heartbeat"] = time.time()

    def new_process(self, id: str) -> None:
        """
        Register a new process for the watchdog.
        """
        with self.__mod_lock:
            self.__proccesses.append({"id": id, "heartbeat": -1})

    def remove_process(self, id: str) -> None:
        """
        Remove a process from the watchdog.
        """
        proc = [_ for _ in self.__proccesses if _["id"] == id][0]

        with self.__mod_lock:
            self.__proccesses.remove(proc)

    def is_alive(self, id: str) -> bool:
        """
        Check if a process is still 'alive'.

        A process is considered dead if its last heartbeat was more than `death`
        seconds ago.
        """
        try:
            proc = [_ for _ in self.__proccesses if _["id"] == id][0]
        except IndexError: # no such process, is technically dead
            return False

        if proc["heartbeat"] == -1: # haven't started yet?
            return True
        if time.time() - proc["heartbeat"] > self.death:
            return False
        else:
            return True

    def watch(self) -> None:
        self.running = True
        while self.running:
            for proc in self.__proccesses:
                if not self.is_alive(proc["id"]):
                    logger.info(f"WATCHDOG: Marked process of id: {proc["id"]} as dead!")
                    self.remove_process(proc["id"])
            time.sleep(0.05)


class Radio:
    def __init__(self, api_auth_key: str = "auth"):
        
        self.ports = [8000, 8080, 8888]
        self.running = False

        self.chunk_size = 90000

        self.api_auth_key = api_auth_key

        self.buffer = []

        self.up_time = 0 # total uptime, in seconds
        self.radio_time = 0 # radio time, in seconds. used for new consumers to join roughly at the same position as others

        self.watchdog = Watchdog()
        self.watch_thread = threading.Thread(target=self.watchdog.watch, daemon=True)
        self.producer_thread = threading.Thread(target=self.producer, daemon=True)

    def __parse_mp3_chunk(self, chunk: bytes):
        """
        Parse an MP3 frame header from a chunk and return bitrate, sample_rate, and frame_size.

        Will not reject non-MP3 bytes, be careful what you input!
        """
        bitrates = [
            None, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, None
        ]
        sample_rates = [44100, 48000, 32000, None]

        # Ensure header starts with 0xFF
        if (chunk[0] != 0xFF) or ((chunk[1] & 0xE0) != 0xE0):
            return None  # Not a valid MP3 frame header

        # Parse fields
        bitrate_index = (chunk[2] >> 4) & 0x0F
        sample_rate_index = (chunk[2] >> 2) & 0x03
        padding_bit = (chunk[2] >> 1) & 0x01

        bitrate = bitrates[bitrate_index]
        sample_rate = sample_rates[sample_rate_index]

        if bitrate is None or sample_rate is None:
            return None

        # Calculate frame size
        frame_size = int((144 * bitrate * 1000) / sample_rate + padding_bit)
        duration = 1152 / sample_rate  # Frame duration in seconds for MP3

        return bitrate, sample_rate, frame_size, duration

    def calculate_buffer_duration(self) -> float:
        """
        Calculate the total duration of MP3 data in the buffer.
        
        Currently only supports MP3!
        """
        total_duration = 0.0
        leftover = b''

        for chunk in self.buffer:
            data = leftover + chunk  # Append leftover data from the previous chunk
            position = 0

            while position + 4 <= len(data):  # Ensure enough bytes for a header
                header = data[position:position + 4]
                parsed = self.__parse_mp3_chunk(header)

                if parsed:
                    bitrate, sample_rate, frame_size, duration = parsed
                    total_duration += duration
                    position += frame_size  # Move to the next frame
                else:
                    position += 1  # Shift by one byte and retry

            leftover = data[position:]  # Save any leftover data for the next chunk

        return total_duration

    def find_chunk_by_time(self, target_time: int):
        """
        Find the corresponding buffer chunk in seconds.

        Currently only supports MP3!
        """
        total_duration = 0.0
        leftover = b''

        for i, chunk in enumerate(self.buffer):
            data = leftover + chunk  # Append leftover data from the previous chunk
            position = 0

            while position + 4 <= len(data):  # Ensure enough bytes for a header
                header = data[position:position + 4]
                parsed = self.__parse_mp3_chunk(header)

                if parsed:
                    bitrate, sample_rate, frame_size, duration = parsed
                    total_duration += duration

                    if total_duration >= target_time:
                        # Return the current chunk index and position within the chunk
                        return i, position

                    position += frame_size  # Move to the next frame
                else:
                    position += 1  # Shift by one byte and retry

            leftover = data[position:]  # Save any leftover data for the next chunk

        raise ValueError("Target time exceeds total buffer duration.")

    def add_track(self, track: str):
        with open(track, "rb") as f:
            to_add = []
            while True:
                chunk = f.read(radio.chunk_size)

                if chunk:
                    print(chunk[:10])
                    to_add.append(chunk)
                else:
                    break
            radio.buffer.extend(to_add)

    def producer(self):
        logger.info("finding length of buffer...")
        buffer_length = self.calculate_buffer_duration()
        logger.info(f"calculated buffer length of {buffer_length} seconds.")

        while self.running:
            self.up_time += 1

            if not self.radio_time + 1 > buffer_length:
                self.radio_time += 1
            else:
                self.radio_time = 0 # reset radio time to loop data once we run out

            time.sleep(1)

    def consumer(self, client: socket.socket):
        i = 0
        id = uuid.uuid4()

        self.watchdog.new_process(id)

        join_chunk, offset = self.find_chunk_by_time(self.radio_time)

        logger.info(f"new consumer of id: '{id}' established. joining at chunk {join_chunk} ({self.radio_time} sec).")

        i = join_chunk

        while self.running and self.watchdog.is_alive(id):
            # if i >= len(self.buffer):
            #     logger.debug("closing connection!")
            #     client.send(b"")
            #     client.close()
            #     i = 0
            #     break
            try:
                if i >= len(self.buffer):
                    i = 0
                    logger.debug(f"({id}) repeat!")

                logger.debug(f"({id}) sending data: {self.buffer[i][:3]}...")
                client.send(self.buffer[i])
                self.watchdog.beat(id)
                i += 1

                logger.debug(f"({id}) waiting on confirmation...")
                resp = client.recv(4)
                logger.debug(f"({id}) confirmation recieved. ({resp})")
                
                if b"RECV" not in resp:
                    logger.warning(f"({id}) non RECV packet! ({resp})")
                    client.close()
                    break

                time.sleep(0.001)
            except ConnectionResetError:
                logger.info(f"connection with consumer of id: '{id}' was reset. exiting.")
                break
        try:
            self.watchdog.remove_process(id)
        except IndexError: # process is already dead, probably killed by watchdog
            client.close()
        logger.info(f"consumer of id: '{id}' exiting gracefully!")

    def main(self):
        self.running = True
        connect = False

        self.watch_thread.start()
        self.producer_thread.start()

        for port in self.ports:
            try:
                server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server_socket.bind(('', port))
                server_socket.listen()
                logger.info(f"Listening on port {port}...")
                connect = True
                break
            except Exception as e:
                continue
        
        if not connect:
            logger.error("startup failure")
            return
        

        while self.running:
            server_socket.settimeout(5)
            try:
                client_connection, client_addr = server_socket.accept()
                client_data = client_connection.recv(20000)
            except TimeoutError:
                continue
            except ConnectionResetError:
                continue

            if client_data.startswith(b"CONN"): # consumer request
                conn_thread = threading.Thread(target=self.consumer, args=(client_connection, ), daemon=True)
                conn_thread.start()
            if client_data.startswith(b"TADD"): # track addition request
                client_data = client_data.split(b" ")
                # TADD <AUTHKEY> <TRACK>
                try:
                    authkey = client_data[1]
                    track = client_data[2]
                except IndexError:
                    client_connection.send(b"INVL")
                    client_connection.close()
                    continue

                if authkey.decode() == self.api_auth_key:
                    if not os.path.exists(track.decode()):
                        client_connection.send(b"NFND")
                        client_connection.close()
                        continue

                    radio.add_track(track.decode())
                    client_connection.send(b"TADD")
                    client_connection.close()

                else:
                    client_connection.send(b"AUTH")
                    client_connection.close()
                    continue

            time.sleep(0.005)

if __name__ == "__main__":
    radio = Radio("authkey")

    logger.info("loading...")
    radio.add_track("test.mp3")
    radio.add_track("test2.mp3")
    radio.add_track("test_lowbitrate.mp3")
        # radio.buffer = radio.buffer[len(radio.buffer)-10:]

    # with open("test_lowbitrate.mp3", "rb") as f:
    #     while True:
    #         chunk = f.read(radio.chunk_size)

    #         if chunk:
    #             # print(chunk[:10])
    #             radio.buffer.append(chunk)
    #         else:
    #             break
    try:
        radio.main()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt raised!")
        logger.info("waiting for Watchdog and Producer to shut down...")
        radio.watchdog.running = False
        radio.running = False
        radio.watch_thread.join()
        radio.producer_thread.join()

        logger.info(f"Radio exited successfully after {radio.up_time} sec of uptime.")
