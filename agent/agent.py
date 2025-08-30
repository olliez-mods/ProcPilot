import time

import serviceManager
import hosting

manager = serviceManager.ServiceManager()

manager.load_service_configs("services.json")
manager.start_startup_services()

hosting.initialize_server_socket()
hosting.set_service_manager(manager)

for service in manager.get_services():
  print(f"Service: {service.name} (ID: {service.id}) - Status: {service.is_running()}")
  # service.stop_service()

while(True):
  manager.tick()
  hosting.tick()
  time.sleep(0.2)