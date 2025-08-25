import json
import subprocess
import time
import os

LOGFILE = "/tmp"

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
    self.log_file = f"{LOGFILE}/{self.name_standardized}.log"

    self.last_log_pos = 0

    self._last_is_running_check_stale_timeout_ = 0.5
    self._last_is_running_check_ = 0
    self._last_is_running_check_result_ = False

  @staticmethod
  def standardize_name(name: str) -> str:
    return name.strip().lower().replace(" ", "_")

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

    # Create tmux startup command
    tmux_cmd = [ "tmux", "new-session", "-d", "-s", self.tmux_session_name]
    if(self.start_directory):
      tmux_cmd.extend(["-c", self.start_directory])
    tmux_cmd.append(self.startup_command)

    subprocess.run(tmux_cmd, stdout=subprocess.DEVNULL)
    subprocess.run([  # pipe logs to file
      "tmux", "pipe-pane", "-t", self.tmux_session_name,
      "-o", f"cat >> {self.log_file}"
    ], stdout=subprocess.DEVNULL)
    return True
  
  def stop_service(self) -> bool:
    if not self.is_running(True): return False # Service is not running
    subprocess.run(["tmux", "kill-session", "-t", self.tmux_session_name],
                   stdout=subprocess.DEVNULL)
    return True
  
  def restart_service(self) -> bool:
    self.stop_service()
    return self.start_service()

  def get_new_log_lines(self, last_pos=None) -> tuple[list[str], int]:
    """
    Get new log lines from the service's log file.
    Returns a tuple of the new log lines and the new file position.
    """
    if last_pos is None: last_pos = self.last_log_pos
    lines = []
    with open(self.log_file, "r") as f:
      f.seek(last_pos)
      lines = f.readlines()
      self.last_log_pos = f.tell()
    return lines, self.last_log_pos
  
  def clear_log_file(self) -> bool:
    try:
      with open(self.log_file, "w") as f:
        f.truncate(0)
      self.last_log_pos = 0
      return True
    except Exception as e:
      print(f"Error clearing log file for {self.name}: {e}")
      return False

class ServiceManager():
  def __init__(self):
    self.services: dict[str, Service] = {}

  def save_service_configs(self, config_file: str) -> bool:
    services_data = [
      {
        "id": service.id,
        "name": service.name,
        "start_directory": service.start_directory,
        "startup_command": service.startup_command,
        "shutdown_command": service.shutdown_command,
        "auto_start": service.auto_start
      }
      for service in self.services.values()
    ]
    with open(config_file, "w") as f:
      json.dump(services_data, f)
    return True

  def load_service_configs(self, config_file: str) -> bool:
    self.services.clear()
    with open(config_file, "r") as f:
      try: services_data:list[dict] = json.load(f)
      except json.JSONDecodeError: return False
      for service_dict in services_data:
        id = service_dict.get("id", None) # Not allowed to be missing
        name = service_dict.get("name", None) # Not allowed to be missing
        start_directory = service_dict.get("start_directory", "")
        if start_directory: start_directory = os.path.expanduser(start_directory)
        startup_command = service_dict.get("startup_command", "")
        shutdown_command = service_dict.get("shutdown_command", "")
        auto_start = service_dict.get("auto_start", False)

        if(not id or not name): continue # Ignore service

        service = Service(id=id, name=name)
        service.start_directory = start_directory
        service.startup_command = startup_command
        service.shutdown_command = shutdown_command
        service.auto_start = auto_start
        self.services[service.id] = service
    return True
  
  def get_service_by_id(self, id:str) -> Service|None:
    return self.services.get(id, None)

  def get_service_by_name(self, name:str) -> Service|None:
    for service in self.services.values():
      if service.name == name: return service
    return None

  def start_startup_services(self):
    for service in self.services.values():
      if service.auto_start and not service.is_running(True):
        service.clear_log_file()
        service.start_service()

  def print_all_new_service_logs(self):
    for service in self.services.values():
      new_lines, _ = service.get_new_log_lines()
      if new_lines:
        print(f"[{service.name}]\n{''.join(new_lines)}")