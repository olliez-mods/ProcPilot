from __future__ import annotations

from io import TextIOWrapper
import json
import subprocess
import time
import os
import re

from typing import Optional, Union

LOGFILE_FOLDER = "."
MAX_LOG_READ_SIZE = 1024 * 992 # 992KB max read size (just under 1MB packet limit)

# Tracks a specific log file
class Log():

  class Marker():
    def __init__(self, name: str, timestamp: int, tags: list[str], pos: int, line_num: int):
      self.name = name
      self.tags = tags or []
      self.timestamp = timestamp
      self.pos = pos
      self.line_num = line_num

    def to_string(self) -> str:
      timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(self.timestamp))
      marker_str = f"--- [PROCPILOT] {self.name} [{','.join(self.tags)}] ({timestamp_str}) ---"
      return marker_str

    @staticmethod
    def create_str(name: str, tags: list[str] = None,) -> Log.Marker:
      """Create new marker with minimal information"""
      marker = Log.Marker(name, int(time.time()), tags or [], 0, 0)
      return marker

    @staticmethod
    def from_line(line: str) -> Log.Marker|None:
      """Create new marker from log line"""
      if(not line): return None
      if(not line.startswith("--- [PROCPILOT]")): return None

      # Example marker: --- [PROCPILOT] MARKER_NAME [tags] (YYYY-MM-DD HH:MM:SS) ---
      match = re.search(r"--- \[PROCPILOT\] (.+?) \[(.*?)\] \(([^)]+)\) ---", line)
      if not match: return None

      marker_name = match.group(1)
      marker_tags = match.group(2).split(",") if match.group(2) else []

      try: marked_time = int(time.mktime(time.strptime(match.group(3), "%Y-%m-%d %H:%M:%S")))
      except Exception: return None

      return Log.Marker(marker_name, marked_time, marker_tags, 0, 0)

  class SessionInfo():
    def __init__(self):
      self.start_pos:int = None
      self.end_pos:int = None
      self.markers: list[Log.Marker] = []
      self.start_time: float | None = None
      self.end_time: float | None = None

  def __init__(self, file_path:str, max_read_size: int = MAX_LOG_READ_SIZE):
    self.file_path = file_path
    self.last_pos = 0
    self.max_read_size = max_read_size

    self.old_sessions: list[Log.SessionInfo] = []

    self.current_session: Optional[Log.SessionInfo] = None
    self.current_pos: int = 0
    self.current_line: int = 0
    self.current_line_start: int = 0

  def __end_session(self, end_pos: int, end_time: float|None = None):
    """Ends the current session."""
    if self.current_session:
      self.current_session.end_pos = end_pos
      self.current_session.end_time = end_time
      self.old_sessions.append(self.current_session)
      self.current_session = None

  def __handle_line(self, line:str):
    line_marker = Log.Marker.from_line(line)
    if(not line_marker): return

    line_marker.pos = self.current_line_start
    line_marker.line_num = self.current_line

    if line_marker.name == "START": # Force start new session, save old session if available
      self.__end_session(line_marker.pos, line_marker.timestamp)
      # Start new session and add marker
      self.current_session = Log.SessionInfo()
      self.current_session.start_pos = line_marker.pos
      self.current_session.start_time = line_marker.timestamp
      self.current_session.markers.append(line_marker)

    if(not self.current_session): return

    if line_marker.name in ("SHUTDOWN", "STOP", "ERROR"): # Known stop (includes timestamp), end session
      self.current_session.markers.append(line_marker)
      self.__end_session(line_marker.pos, line_marker.timestamp)
    else:
      self.current_session.markers.append(line_marker)

  def handle_new_lines(self):
    """Handles new, unread, lines in log file, new sessions, markers, etc."""
    with self.__open() as f:
      f.seek(self.current_pos) # Move to last read position
      while True:
        line_start_pos = f.tell() # Temp line start position
        line = f.readline()
        if not line: break
        self.current_line += 1
        self.current_pos = f.tell()
        self.current_line_start = line_start_pos # Fullfill
        self.__handle_line(line)

  def __open(self, mode:str = "r") -> TextIOWrapper:
    # Ensure the directory exists before opening the file
    dir_path = os.path.dirname(self.file_path)
    if dir_path and not os.path.exists(dir_path):
      os.makedirs(dir_path, exist_ok=True)
    # Ensure the file exists
    if not os.path.exists(self.file_path):
      open(self.file_path, "a").close()
    return open(self.file_path, mode)

  def read_after(self, position:int) -> tuple[str, int]:
    """
    Reads the log file from a specific position to the end.
    Returns the read content and the end position.
    """
    with self.__open() as f:
      f.seek(position)
      content = f.read(self.max_read_size)
      return content, f.tell()

  def read_end(self, num_bytes: int, backwards_offset: int = 0, respect_current_session: bool = True) -> tuple[str, int]:
    """
    Read the last num_bytes bytes from the log file
    respecting max_read_size
    Optional backwards_offset can be used to offset the read position
    If respect_current_session is True, the read position will be capped at the current session's start position
    returns the read content and the end position.
    """
    with self.__open() as f:
      f.seek(0, os.SEEK_END)
      file_size = f.tell()
      if file_size == 0: return "", 0
      # Calculate actual read size respecting max_read_size
      actual_read_size = min(num_bytes, self.max_read_size)

      # Calculate start position with backwards_offset (cap to 0)
      start_pos = file_size - actual_read_size - backwards_offset
      start_pos = max(0, start_pos)

      # If respecting session, don't overread into old sessions
      if(respect_current_session and self.current_session and self.current_session.start_pos != None):
        start_pos = max(start_pos, self.current_session.start_pos)
        actual_read_size = min(actual_read_size, file_size - start_pos)

      f.seek(start_pos)
      data = f.read(actual_read_size)
      return data, f.tell()

  def write_marker(self, marker:Log.Marker):
    """implant_timestamp - Looks for 'timestamp' in string and replaces it with the current timestamp"""
    m_str = marker.to_string()
    with self.__open("a") as f:
      f.write(m_str + "\n")

  # Write startup marker to log file (for tracking and corruption resolving)
  def write_startup_marker(self):
    self.write_marker(Log.Marker.create_str("START"))

  def write_stop_marker(self, reason: str = "N/A"):
    self.write_marker(Log.Marker.create_str(f"STOP", [reason]))

  @staticmethod
  def get_log_file_path(id: str) -> str:
    return f"{LOGFILE_FOLDER}/ProcPilot_log_{id}.log"

class Service():
  def __init__(self, id: str, name: str):
    self.id: str = id
    self.name: str = name
    self.name_standardized: str = Service.standardize_name(name)
    self.start_directory: str = ""
    self.startup_command: str = ""
    self.shutdown_command: str = ""
    self.auto_start: bool = False

    self.tmux_session_name = f"{self.name_standardized}_ProcPilot_{self.id[:8]}"

    self.log_file_path = Log.get_log_file_path(self.id)
    self.log = Log(self.log_file_path)
    self.log.handle_new_lines()  # Initial read to set positions

    self.last_log_pos = 0

    self._last_is_running_check_stale_timeout_ = 0.5
    self._last_is_running_check_ = 0
    self._last_is_running_check_result_ = False

  @staticmethod
  def standardize_name(name: str) -> str:
    return name.strip().lower().replace(" ", "_")
  
  @staticmethod
  def from_json(data: dict) -> Service:
    try:
      service = Service(id=data["id"], name=data["name"])
      service.startup_command = data["startup_command"]
      service.start_directory = data["start_directory"]
    except KeyError as e:
      raise ValueError(f"Missing required field: {e}")
    service.shutdown_command = data.get("shutdown_command", "")
    service.auto_start = data.get("auto_start", False)
    return service

  def to_json(self) -> dict:
    return {
      "id": self.id,
      "name": self.name,
      "start_directory": self.start_directory,
      "startup_command": self.startup_command,
      "shutdown_command": self.shutdown_command,
      "auto_start": self.auto_start
    }

  # Caches results for faster checks, stales quickly
  def is_running(self, force_refresh=False) -> bool:
    now = time.time()
    if(force_refresh or (now - self._last_is_running_check_ > self._last_is_running_check_stale_timeout_)):
      result = subprocess.run(["tmux", "has-session", "-t", self.tmux_session_name],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
      self._last_is_running_check_result_ = (result.returncode == 0)
      self._last_is_running_check_ = now
    return self._last_is_running_check_result_

  def start_service(self) -> bool:
    if(self.is_running(True)): return False # Service already running

    self.log.write_startup_marker()

    # Create tmux startup command
    tmux_cmd = [ "tmux", "new-session", "-d", "-s", self.tmux_session_name]
    if(self.start_directory):
      tmux_cmd.extend(["-c", self.start_directory])
    tmux_cmd.append(self.startup_command)

    subprocess.run(tmux_cmd, stdout=subprocess.DEVNULL)
    subprocess.run([  # pipe logs to file
      "tmux", "pipe-pane", "-t", self.tmux_session_name,
      "-o", f"cat >> {self.log_file_path}"
    ], stdout=subprocess.DEVNULL)
    return True
  
  def stop_service(self) -> bool:
    if not self.is_running(True): return False # Service is not running
    subprocess.run(["tmux", "kill-session", "-t", self.tmux_session_name],
                   stdout=subprocess.DEVNULL)
    self.log.write_stop_marker()
    return True
  
  def restart_service(self) -> bool:
    self.stop_service()
    return self.start_service()

  def send_byte(self, byte: bytes) -> bool:
    if not self.is_running(True): return False # Service is not running
    subprocess.run(["tmux", "send-keys", "-t", self.tmux_session_name, byte])
    return True

class ServiceManager():
  def __init__(self):
    self.services: dict[str, Service] = {}

  def save_service_configs(self, config_file: str) -> bool:
    services_data = [service.to_json() for service in self.services.values()]
    with open(config_file, "w") as f:
      json.dump(services_data, f, indent=2)
    return True

  def load_service_configs(self, config_file: str) -> bool:
    self.services.clear()
    with open(config_file, "r") as f:
      try: services_data:list[dict] = json.load(f)
      except json.JSONDecodeError: return False
      for service_dict in services_data:
        try:
          service:Service = Service.from_json(service_dict)
          self.services[service.id] = service
        except ValueError:
          print(f"Invalid service configuration: {service_dict}")
          continue
    return True
  
  def get_keyed_services(self) -> dict[str, Service]:
    return self.services

  def get_services(self) -> list[Service]:
    return list(self.services.values())

  def get_service_by_id(self, id:str) -> Service|None:
    return self.services.get(id, None)

  def get_service_by_name(self, name:str) -> Service|None:
    for service in self.services.values():
      if service.name == name: return service
    return None
  
  def tick(self):
    for service in self.get_services():
      service.log.handle_new_lines()
      if(service.auto_start and not service.is_running(True)):
        service.start_service()
        print(f"Auto-started service: {service.name}")


# Testing functions
  def start_startup_services(self):
    for service in self.services.values():
      if service.auto_start and not service.is_running(True):
        service.start_service()

  def print_all_new_service_logs(self):
    for service in self.services.values():
      new_lines, _ = service.get_new_log_lines()
      if new_lines:
        print(f"[{service.name}]\n{''.join(new_lines)}")