import socket
import time
import threading
import logging
import uuid
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
        logger.debug(time.time() - proc["heartbeat"])
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
    def __init__(self):
        
        self.ports = [8000, 8080, 8888]
        self.running = False

        self.chunk_size = 90000

        self.buffer = []
        self.buffer_index = 0

        self.watchdog = Watchdog()
        self.watch_thread = threading.Thread(target=self.watchdog.watch, daemon=True)

        self.queue = deque(maxlen=20)

    def producer(self):
        while self.running:
            # self.queue.append(''.join(random.sample("abdechafhfajksdfhdjkJKLSHSDFJKHSDKFJHDS", random.randint(1, 10))))
            self.queue.append(self.buffer[self.buffer_index])
            self.buffer_index += 1
            time.sleep(0.05)

    def consumer(self, client: socket.socket):
        i = 0
        id = uuid.uuid4()

        self.watchdog.new_process(id)

        logger.info(f"new consumer of id: '{id}' established.")

        while self.running and self.watchdog.is_alive(id):
            # if i >= len(self.buffer):
            #     logger.debug("closing connection!")
            #     client.send(b"")
            #     client.close()
            #     i = 0
            #     break

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
        
        try:
            self.watchdog.remove_process(id)
        except IndexError: # process is already dead, probably killed by watchdog
            client.close()
        logger.info(f"consumer of id: '{id}' exiting gracefully!")

    def main(self):
        self.running = True
        connect = False

        self.watch_thread.start()

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
        
        # producer_thread = threading.Thread(target=self.producer, daemon=True)
        # producer_thread.start()

        while self.running:
            server_socket.settimeout(5)
            try:
                client_connection, client_addr = server_socket.accept()
                client_data = client_connection.recv(20000)
            except TimeoutError:
                continue
            except ConnectionResetError:
                continue

            if client_data.startswith(b"CONN"):
                conn_thread = threading.Thread(target=self.consumer, args=(client_connection, ), daemon=True)
                conn_thread.start()

            time.sleep(0.005)

if __name__ == "__main__":
    radio = Radio()

    logger.info("loading...")
    with open("test.aac", "rb") as f:
        while True:
            chunk = f.read(radio.chunk_size)

            if chunk:
                print(chunk[:10])
                radio.buffer.append(chunk)
            else:
                break

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
        logger.info("waiting for Watchdog to shut down...")
        radio.watchdog.running = False
        radio.watch_thread.join()
