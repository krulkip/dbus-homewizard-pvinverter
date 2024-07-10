#!/usr/bin/env python

# import normal packages
import platform
import logging
import logging.handlers
import sys
import os
import sys
if sys.version_info.major == 2:
    import gobject
else:
    from gi.repository import GLib as gobject
import sys
import time
import requests # for http GET
import configparser # for config/ini file

# our own packages from victron
sys.path.insert(1, os.path.join(os.path.dirname(__file__), '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python'))
from vedbus import VeDbusService


class DbusHomewizardPVService:
  def __init__(self, servicename, paths, productname='Homewizard kWh PV inverter', connection='Homewizard PV HTTP JSON service'):
    config = self._getConfig()
    deviceinstance = int(config['DEFAULT']['Deviceinstance'])
    customname = config['DEFAULT']['CustomName']
    role = config['DEFAULT']['Role']
    
    allowed_roles = ['pvinverter','grid']
    if role in allowed_roles:
        servicename = 'com.victronenergy.' + role
    else:
        logging.error("Configured Role: %s is not in the allowed list")
        exit()

    if role == 'pvinverter':
         productid = 0xA144
    else:
         productid = 45069

    self._dbusservice = VeDbusService("{}.http_{:02d}".format(servicename, deviceinstance))
    self._paths = paths

    # Create the management objects, as specified in the ccgx dbus-api document
    self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
    self._dbusservice.add_path('/Mgmt/ProcessVersion', 'Unkown version, and running on Python ' + platform.python_version())
    self._dbusservice.add_path('/Mgmt/Connection', connection)

    # Create the mandatory objects
    self._dbusservice.add_path('/DeviceInstance', deviceinstance)
    self._dbusservice.add_path('/ProductId', 0xA144) # id assigned by Victron Support from SDM630v2.py
    self._dbusservice.add_path('/DeviceType', 345)  
    self._dbusservice.add_path('/ProductName', productname)
    self._dbusservice.add_path('/CustomName', customname)
    self._dbusservice.add_path('/Latency', None)
    self._dbusservice.add_path('/FirmwareVersion', 0.2)
    self._dbusservice.add_path('/HardwareVersion', 0)
    self._dbusservice.add_path('/Connected', 1)
    self._dbusservice.add_path('/Role', role)

    self._dbusservice.add_path('/Position', self._getHomewizardPosition())
    self._dbusservice.add_path('/Serial', self._getHomewizardSerial())
    self._dbusservice.add_path('/UpdateIndex', 0)
    self._dbusservice.add_path('/StatusCode', 0)  # Dummy path so VRM detects us as a PV-inverter.

    # add path values to dbus
    for path, settings in self._paths.items():
      self._dbusservice.add_path(
        path, settings['initial'], gettextcallback=settings['textformat'], writeable=True, onchangecallback=self._handlechangedvalue)

    # last update
    self._lastUpdate = 0

    # add _update function 'timer'
    gobject.timeout_add(500, self._update) # pause 500ms before the next request

    # add _signOfLife 'timer' to get feedback in log every 5minutes
    gobject.timeout_add(self._getSignOfLifeInterval()*60*1000, self._signOfLife)

  def _getHomewizardSerial(self):
    meter_data = self._getHomewizardData()

    if not meter_data['unique_id']:
        raise ValueError("Response does not contain 'unique_id' attribute")

    serial = meter_data['unique_id']
    return serial

  def _getConfig(self):
    config = configparser.ConfigParser()
    config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))
    return config;


  def _getSignOfLifeInterval(self):
    config = self._getConfig()
    value = config['DEFAULT']['SignOfLifeLog']

    if not value:
        value = 0

    return int(value)

  def _getHomewizardPosition(self):
    config = self._getConfig()
    value = config['DEFAULT']['Position']

    if not value:
        value = 0

    return int(value)
    
  def _getHomewizardStatusUrl(self):
        config = self._getConfig()
        accessType = config['DEFAULT']['AccessType']
        
        if accessType == 'OnPremise': 
            URL = "http://%s/api/v1/data" % (config['ONPREMISE']['Host'])
            # URL = URL.replace(":@", "")
        else:
            raise ValueError("AccessType %s is not supported" % (config['DEFAULT']['AccessType']))
        
        return URL


  def _getHomewizardData(self):
    URL = self._getHomewizardStatusUrl()
    meter_r = requests.get(url = URL, timeout=5)

    # check for response
    if not meter_r:
        raise ConnectionError("No response from Homewizard - %s" % (URL))

    meter_data = meter_r.json()

    # check for Json
    if not meter_data:
        raise ValueError("Converting response to JSON failed")

    return meter_data


  def _signOfLife(self):
    logging.info("--- Start: sign of life ---")
    logging.info("Last _update() call: %s" % (self._lastUpdate))
    logging.info("Last '/Ac/Power': %s" % (self._dbusservice['/Ac/Power']))
    logging.info("--- End: sign of life ---")
    return True

  def _update(self):
    try:
       #get data from Homewizard
       meter_data = self._getHomewizardData()
       config = self._getConfig()
        
       str(config['DEFAULT']['Phase'])
       pvinverter_phase = str(config['DEFAULT']['Phase'])

       #send data to DBus
       for phase in ['L1', 'L2', 'L3']:
         pre = '/Ac/' + phase

         if phase == pvinverter_phase:       
           self._dbusservice[pre + '/Voltage'] = meter_data['active_voltage_v']
           self._dbusservice[pre + '/Current'] = meter_data['active_current_a']
           self._dbusservice[pre + '/Power'] = meter_data['active_power_w']

         else:
           self._dbusservice[pre + '/Voltage'] = 0
           self._dbusservice[pre + '/Current'] = 0
           self._dbusservice[pre + '/Power'] = 0
           self._dbusservice[pre + '/Energy/Forward'] = 0

      self.dbusservice['/Ac/Power'] = meter_data['active_power_w']
      self._dbusservice['/Ac/Energy/Forward'] = meter_data['total_power_import_kwh']
      self._dbusservice['/Ac/Energy/Reverse'] = meter_data['total_power_export_kwh']

       #logging
       logging.debug("House Consumption (/Ac/Power): %s" % (self._dbusservice['/Ac/Power']))
       logging.debug("House Forward (/Ac/Energy/Forward): %s" % (self._dbusservice['/Ac/Energy/Forward']))
       logging.debug("House Forward (/Ac/Energy/Reverse): %s" % (self._dbusservice['/Ac/Energy/Reverse']))
       logging.debug("---");

       # increment UpdateIndex - to show that new data is available
       self._dbusservice['/UpdateIndex'] = (self._dbusservice['/UpdateIndex'] + 1) % 256  # increment index

       #update lastupdate vars
       self._lastUpdate = time.time()
    except (ValueError, requests.exceptions.ConnectionError, requests.exceptions.Timeout, ConnectionError) as e:
            logging.critical('Error getting data from Homewizard - check network or Homewizard status. Setting power values to 0. Details: %s', e, exc_info=e)       
            self._dbusservice['/Ac/L1/Power'] = 0                                       
            self._dbusservice['/Ac/L2/Power'] = 0                                       
            self._dbusservice['/Ac/L3/Power'] = 0
            self._dbusservice['/Ac/Power'] = 0
            self._dbusservice['/UpdateIndex'] = (self._dbusservice['/UpdateIndex'] + 1 ) % 256 
    except Exception as e:
       logging.critical('Error at %s', '_update', exc_info=e)

    # return true, otherwise add_timeout will be removed from GObject - see docs http://library.isr.ist.utl.pt/docs/pygtk2reference/gobject-functions.html#function-gobject--timeout-add
    return True

  def _handlechangedvalue(self, path, value):
    logging.debug("someone else updated %s to %s" % (path, value))
    return True # accept the change

def getLogLevel():
    config = configparser.ConfigParser()
    config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))
    logLevelString = config['DEFAULT']['LogLevel']
    
    if logLevelString:
        level = logging.getLevelName(logLevelString)
    else:
        level = logging.INFO
        
    return level


def main():
  #configure logging
  logging.basicConfig(      format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S',
                            level=logging.INFO,
                            handlers=[
                                logging.FileHandler("%s/current.log" % (os.path.dirname(os.path.realpath(__file__)))),
                                logging.StreamHandler()
                            ])

  try:
      logging.info("Start");

      from dbus.mainloop.glib import DBusGMainLoop
      # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
      DBusGMainLoop(set_as_default=True)

      #formatting
      _kwh = lambda p, v: (str(round(v, 2)) + 'kWh')
      _a = lambda p, v: (str(round(v, 1)) + 'A')
      _w = lambda p, v: (str(round(v, 1)) + 'W')
      _v = lambda p, v: (str(round(v, 1)) + 'V')

      #start our main-service
      pvac_output = DbusHomewizardPVService(
        servicename='com.victronenergy.pvinverter',
        paths={
          '/Ac/Energy/Forward': {'initial': 0, 'textformat': _kwh}, # energy produced by pv inverter
          '/Ac/Power': {'initial': 0, 'textformat': _w},

          '/Ac/Current': {'initial': 0, 'textformat': _a},
          '/Ac/Voltage': {'initial': 0, 'textformat': _v},

          '/Ac/L1/Voltage': {'initial': 0, 'textformat': _v},
          '/Ac/L2/Voltage': {'initial': 0, 'textformat': _v},
          '/Ac/L3/Voltage': {'initial': 0, 'textformat': _v},
          '/Ac/L1/Current': {'initial': 0, 'textformat': _a},
          '/Ac/L2/Current': {'initial': 0, 'textformat': _a},
          '/Ac/L3/Current': {'initial': 0, 'textformat': _a},
          '/Ac/L1/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L2/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L3/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L1/Energy/Forward': {'initial': 0, 'textformat': _kwh},
          '/Ac/L2/Energy/Forward': {'initial': 0, 'textformat': _kwh},
          '/Ac/L3/Energy/Forward': {'initial': 0, 'textformat': _kwh},
        })

      logging.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
      mainloop = gobject.MainLoop()
      mainloop.run()
  except Exception as e:
    logging.critical('Error at %s', 'main', exc_info=e)
if __name__ == "__main__":
  main()
