import socket
import time
import threading
import logging
from collections import deque

logging.basicConfig(#filename="radio.log",
                    format='%(asctime)s:%(name)s/%(levelname)s: %(message)s',
                    filemode='w', datefmt='%I:%M:%S %p')

# Creating an object
logger = logging.getLogger()

# Setting the threshold of logger to DEBUG
logger.setLevel(logging.DEBUG)
logger.name = "RadioServer"

class Radio:
    def __init__(self):
        
        self.ports = [8000, 8080, 8888]
        self.running = False

        self.chunk_size = 90000

        self.buffer = []
        self.buffer_index = 0

        self.queue = deque(maxlen=20)

    def producer(self):
        while self.running:
            # self.queue.append(''.join(random.sample("abdechafhfajksdfhdjkJKLSHSDFJKHSDKFJHDS", random.randint(1, 10))))
            self.queue.append(self.buffer[self.buffer_index])
            self.buffer_index += 1
            time.sleep(0.05)

    def consumer(self, client: socket.socket):
        i = 0
        while self.running:
            if i >= len(self.buffer):
                logger.debug("closing connection!")
                client.send(b"")
                client.close()
                i = 0
                break

            # if i >= len(self.buffer):
            #     i = 0
            #     logger.debug("repeat!")

            logger.debug(f"sending data: {self.buffer[i][:10]}...")
            client.send(self.buffer[i])
            i += 1

            logger.debug("waiting on confirmation...")
            resp = client.recv(4)
            logger.debug(f"confirmation recieved. ({resp})")
            
            if b"RECV" not in resp:
                logger.warning(f"non RECV packet! ({resp})")
                client.close()
                break

            time.sleep(0.001)

    def main(self):
        self.running = True
        connect = False

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
            print("startup failure")
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
    with open("test.mp3", "rb") as f:
        while True:
            chunk = f.read(radio.chunk_size)

            if chunk:
                print(chunk[:10])
                radio.buffer.append(chunk)
            else:
                break

        radio.buffer = radio.buffer[len(radio.buffer)-10:]

    with open("test_lowbitrate.mp3", "rb") as f:
        while True:
            chunk = f.read(radio.chunk_size)

            if chunk:
                # print(chunk[:10])
                radio.buffer.append(chunk)
            else:
                break

    radio.main()
