import time

import serviceManager

manager = serviceManager.ServiceManager()

manager.load_service_configs("services.json")
manager.start_startup_services()

while(True):
  time.sleep(2)
  manager.print_all_new_service_logs()