import socket
from hosting import Packet

SOCKET_TMP_PATH = "/tmp/procpilot.sock"

# Raw bytes for PRINT packet
# [TYPE=2][SUBTYPE="PRINT\x00\x00\x00"][LENGTH=12][DATA="Hello world!"]

# Connect and send
client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
client.connect(SOCKET_TMP_PATH)
client.send(Packet.create(Packet.Type.RAW, "PRINT", b"Hello world! lol").to_bytes())
client.send(Packet.create(Packet.Type.RAW, "PRINT", b"Hello world again! lol").to_bytes())
client.close()

print("Sent raw packet")