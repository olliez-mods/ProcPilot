import socket
import os
import time
import json
from typing import Any

import serviceManager


# Path for local ProcPilot client-agent communication
SOCKET_TMP_PATH = "/tmp/procpilot.sock"
DEFAULT_CLIENT_TIMEOUT = 10.0

server_socket: socket.socket = None

managerConnection:'Connection' = None
connections: list['Connection'] = []

manager:serviceManager.ServiceManager = None

# Format: [TYPE (1)] [SUBTYPE (8, padded)] [LENGTH (4)] [DATA (length)]
class Packet():
  class Type:
    RAW = 1
    JSON = 2

  MAX_SUBTYPE_LEN = 8
  MAX_PACKET_SIZE = 1024 * 1024  # 1MB limit

  def __init__(self, packet_type:int, packet_sub_type:str, raw_bytes:bytes):
    if(len(packet_sub_type) > Packet.MAX_SUBTYPE_LEN):
      raise ValueError(f"Sub-type too long (max {Packet.MAX_SUBTYPE_LEN} characters)")

    self.type = packet_type
    self.sub_type = packet_sub_type
    self.bytes = raw_bytes
    self.full_size = 13 + len(raw_bytes) # Size of entire packet

    self.json = None
    if(self.type == Packet.Type.JSON):
      try: self.json = json.loads(raw_bytes.decode('utf-8'))
      except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON payload: {e}")

  @staticmethod
  def get_length(length_bytes: bytes) -> int: # Expects buffer length 4
    if(len(length_bytes) < 4): return -1 # Not enough data for type and length
    length = (length_bytes[0] << 24) | (length_bytes[1] << 16) | (length_bytes[2] << 8) | length_bytes[3]
    return length
  @staticmethod
  def to_length(length: int) -> bytes:
    length_bytes = bytes([
      (length >> 24) & 0xFF,  # Most significant byte
      (length >> 16) & 0xFF,
      (length >> 8) & 0xFF,
      length & 0xFF           # Least significant byte
    ])
    return length_bytes
  @staticmethod
  def check_complete(buffer: bytes) -> bool:
    if(len(buffer) < 13): return False # Not enough data for type, subtype and length
    length = (Packet.get_length(buffer[9:13]))
    return len(buffer) >= 13 + length  # Check if we have header + full payload
  @staticmethod
  def from_buffer(buffer: bytes) -> 'Packet':
    if(not Packet.check_complete(buffer)): raise ValueError("Buffer does not contain a complete packet")
    packet_type = buffer[0] # 1 is raw, 2 is JSON, 3+ is not used yet, so ignored
    try: packet_sub_type = buffer[1:9].rstrip(b'\x00').decode('utf-8')
    except UnicodeDecodeError: raise ValueError("Invalid sub-type encoding")
    length = Packet.get_length(buffer[9:13])
    payload = buffer[13:13+length]
    return Packet(packet_type, packet_sub_type, payload)
  @staticmethod
  def create(packet_type:int, packet_sub_type:str, data:bytes|str|dict) -> 'Packet':
    """
    Create a packet.
    Pass in raw bytes, a string, or a dictionary (for JSON).
    """

    raw_bytes = b""
    if(packet_type == Packet.Type.JSON):
      if isinstance(data, dict): raw_bytes = json.dumps(data).encode('utf-8')
      else: raise ValueError("For JSON packets, data must be a dictionary")
    elif(packet_type == Packet.Type.RAW):
      if isinstance(data, bytes): raw_bytes = data
      elif isinstance(data, str): raw_bytes = data.encode('utf-8')
      else: raise ValueError("For RAW packets, data must be bytes or a string")
    else:
      raise ValueError("Unknown packet type")
    return Packet(packet_type, packet_sub_type, raw_bytes)

  def to_bytes(self) -> bytes:
    # 1. TYPE (1 byte)
    packet_bytes = bytes([self.type])
    #2. SUBTYPE (8 bytes, with padding)
    subtype_bytes = self.sub_type.encode('utf-8')[:8].ljust(8, b'\x00')
    packet_bytes += subtype_bytes
    # 3. LENGTH (4 bytes, big-endian)
    length = len(self.bytes)
    packet_bytes += Packet.to_length(length)
    # 4. DATA (use self.bytes)
    packet_bytes += self.bytes
    return packet_bytes

class Connection():
  def __init__(self, sock: socket.socket):
    self.socket = sock
    self.buffer = b""
    try: self.address = sock.getpeername()
    except (OSError, AttributeError): self.address = "unix_socket"
    self.last_active = time.time()
    self.closed = False
  
  def close(self):
    self.socket.close()
    self.closed = True

  # Recv waiting buffer
  def fill_buffer(self):
    try:
      data = self.socket.recv(4096)
      if data:
        self.buffer += data
        self.last_active = time.time()
      else:
        self.close()
    except BlockingIOError: pass  # No data available right now
    except Exception as e:
      print(f"Error reading from {self.address}: {e}")
      self.close()
  
  def send_packet(self, packet:Packet):
    if(self.closed):
      raise ConnectionError("Not connected to socket - cannot send packet")
    packet_bytes = packet.to_bytes()
    self.socket.send(packet_bytes)

  def get_next_packet(self) -> Packet|None:
    if not Packet.check_complete(self.buffer): return None
    packet = Packet.from_buffer(self.buffer)
    self.buffer = self.buffer[packet.full_size:]
    return packet

  def check_timeout(self, timeout:float=DEFAULT_CLIENT_TIMEOUT) -> bool:
    if(time.time() - self.last_active > timeout):
      print("Client timed out")
      self.close()
      return False
    return True

def create_unix_socket_server(name=SOCKET_TMP_PATH):
  socket_path = name
  if os.path.exists(socket_path):
      os.unlink(socket_path)

  server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
  server.bind(socket_path)
  server.listen()
  return server

def initialize_server_socket():
  global server_socket
  if server_socket is None:
    server_socket = create_unix_socket_server()
    server_socket.setblocking(False)
    print(f"Server listening on {server_socket.getsockname()}")
  else:
    print("Server socket already initialized.")

def set_service_manager(s_manager:serviceManager.ServiceManager):
  global manager
  manager = s_manager

def kill_server_socket():
  global server_socket
  if server_socket is not None:
    server_socket.close()
    server_socket = None
    print("Server socket closed.")
  else:
    print("Server socket was not initialized.")

# Should be called periodically to handle incoming connections and data
def tick():
  try:
    # Accept new connections if any
    conn, _ = server_socket.accept()
    conn.setblocking(False)
    c = Connection(conn)
    connections.append(c)
    print("Accepted new connection.")
  except BlockingIOError:
    pass  # No incoming connection this tick

  # Remove connections marked as closed
  connections[:] = [c for c in connections if not c.closed]

  for c in connections:
    if(not c.check_timeout()): continue

    # Get new data and try to recv a packet
    c.fill_buffer()
    packet:Packet = c.get_next_packet()
    if(not packet): continue

    if(packet.sub_type == "CLOSE"):
      c.close()

    if(packet.sub_type == "PRINT"):
      print(packet.bytes.decode())
    
    if(packet.sub_type == "PRINT_J"):
      print(packet.json["message"])
