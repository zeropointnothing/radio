# synthLength Radio

*synthLength* is an Online Radio written in Python using Sockets. Now you can host your own personal radio station without the requirement of an expensive license!

*synthLength* is designed to be as easy as possible to set up, use, and monitor.

## Terms and Their Meanings

**Consumer**: The server-side representation of an active listener, or audio *consumer.*

**Producer**: The server-side representation of a radio manager, or *producer.*

**Playlist**: The master track list, providing track info to the status request and initial track loading.

**Buffer**: The master audio track, consisting of all loaded tracks in equal chunks.

## HTTP(S) Server
Originally, synthLength was designed as an HTTP based server.

However, due to difficulties and annoyances of the protocol, a much easier to develop custom protocol was made with Sockets.

The HTTP (FastAPI) based server will remain in this repository for reference only, and will receive no official support. Please use the newer Socket based server if you want support and updates.


## Playlist Formatting

While synthLength does support live track additions, doing so can cause issues for currently running Consumers.

The best supported way to add tracks is through the server Playlist. Tracks specified in here will be loaded at the server's startup.

The format of a `playlist.json` file is as follows:
```json
{
    "media_type": "...",
    "tracks": [
    {
        "title": "...",
        "author": "...",
        "length": 216,
        "path": "..."
    },
    {
        "title": "...",
        "author": "...",
        "length": 150,
        "path": "..."
    },
    {
        "title": "...",
        "author": "...",
        "length": 259,
        "path": "..."
    }
    ]
}
```

An explaination of each value:
- **Media Type**: The intended media type of your Radio. All tracks added here should match this format.
- **Tracks**: A list of tracks. You can repeat the format of these example tracks as many times as you need.
- **Title**: The track's title.
- **Author**: The track's author(s).
- **Length**: The track's duration, in seconds.
- **Path**: The path to track. While you can use a local path (song.mp3), it is suggested that you use an absolute one instead (/path/to/song.mp3).

### Media Types

At the moment, synthLength only supports MP3. This is due to the complicated nature of finding chunk positions by seconds elapsed, requiring specific functions for each format.

Additionally, ensure that you do not mix file formats, as that can cause issues for both the server (seconds to chunks/buffer length functions) and clients.

## Server Requests

*The following is mainly useful for those interested in custom clients. You can skip this if that isn't your use-case.*

*This guide will assume you are using Python and its `socket` library. Adjust all examples for your language's specific implementations.*

There are a few different requests you can make to a synthLength server, though they all follow similar rules.

Before we begin, it is important to know that synthLength will almost *always* respond with 4 bytes. Unless told otherwise, you should set your socket library to receive 4 bytes when getting a response from your request.

### Connect **(CONN)**

This is the most important request of them all. The Connect request establishes a connection with the synthLength server to receive data through.

First, open a connection to the server:
```python
# create a TCP ipv4 socket.
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# replace 192.168.1.123 and 800 with your server's host/port.
sock.connect(("192.168.1.123", 8000))
```

Then, send the CONN packet to establish your connection to the radio:

```python
# tell the server we want to open a radio stream.
sock.send(b"CONN")
```


If all goes well, you will now be connected to the synthLength server! **KEEP THIS CONNECTION OPEN,** as it's your tunnel to receive radio data!

The synthLength server will create a new Consumer for your client, and immediately start sending data. To ensure that your client doesn't fall behind, you should receive and play this as soon as possible:

```python
# receive 50KB chunks of data.
chunk = sock.recv(50000)

# feed the data into your player...
```

If you try to accept more data after this, you might notice something odd. The server won't send any more!

This is because of the nature of sockets. Your client's Consumer *could* send more data, but if your client wasn't ready for it, the chunks would just build up and potentially cause issues. To solve this, the Consumer will wait for a confirmation from your client before sending the next chunk.

To inform the Consumer that your client is ready for more data, send a "RECV" packet back to the server through your still-open connection:

```python
sock.send(b"RECV")
```

Now, it's just rinse and repeat!

Full example:

```python
import socket

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(("192.168.1.123", 8000))

sock.send(b"CONN")

while True:
    chunk = sock.recv(50000)

    if chunk:
        # replace with your code...
        send_chunk_to_player(chunk)
        sock.send(b"RECV")
    else:
        break

# make sure to close your connection when you're done!
sock.close()
```

*Note, that synthLength servers are equipped with a "Watchdog" that monitors all Consumers, killing any that have not responded for a certain amount of time. If you do not close the connection yourself, don't worry, the server should eventually kill your Consumer for you. This also applies to Clients that have been idle too long, so make sure to keep the connection active while you're still using it!*

### Status **(STAT)**

The Status request allows your client to retrieve information about the currently playing track.

First, open a connection to the server:
```python
# create a TCP ipv4 socket.
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# replace 192.168.1.123 and 800 with your server's host/port.
sock.connect(("192.168.1.123", 8000))
```

Then, send the actual "STAT" request:
```python
sock.send(b"STAT")
```

There are two responses you can receive from this request.

On a successful request, you will receive a JSON response with the radio's status. Because this response can vary, you should first accept the first 4 bytes of the response to get the length of the remaining JSON, then receive the rest of the response:
```python
json_length = sock.recv(4)
json = sock.recv(json_length)
```

On an unsuccessful response (no media is playing, or something else is wrong), you will receive a Not Found (NFND) response from the server. Since this is also 4 bytes, you should check for this when trying to get the length of the JSON response:
```python
json_length = sock.recv(4)
if json_length == b"NFND":
    print("Not Found!")
else:
    json = sock.recv(json_length)
```

## Official Client

synthLength is *technically* only the online radio server software. However, for ease-of-use and documentation, an example client has been provided in this repository using Curses as a TUI backend.

It works, but has known limitations and issues. Expect things to break until all of these have been ironed out.
