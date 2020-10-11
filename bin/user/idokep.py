
from __future__ import absolute_import

import datetime
import logging
import platform
import re
import socket
import ssl
import threading
import time
import urllib.request, urllib.parse, urllib.error

# Python 2/3 compatiblity shims
import six
from six.moves import http_client
from six.moves import queue
from six.moves import urllib

import weedb
import weeutil.logger
import weeutil.weeutil
import weewx.engine
import weewx.manager
import weewx.units
import weewx.restx
from weeutil.config import search_up, accumulateLeaves
from weeutil.weeutil import to_int, to_float, to_bool, timestamp_to_string, to_sorted_string
from weewx.restx import StdRESTful, RESTThread, get_site_dict

try:
    # Test for new-style weewx v4 logging by trying to import weeutil.logger
    import weeutil.logger
    import logging

    log = logging.getLogger(__name__)

    def logdbg(msg):
        log.debug(msg)

    def loginf(msg):
        log.info(msg)

    def logerr(msg):
        log.error(msg)

except ImportError:
    # Old-style weewx logging
    import syslog

    def logmsg(level, msg):
        syslog.syslog(level, 'IDOKEP: %s' % msg)

    def logdbg(msg):
        logmsg(syslog.LOG_DEBUG, msg)

    def loginf(msg):
        logmsg(syslog.LOG_INFO, msg)

    def logerr(msg):
        logmsg(syslog.LOG_ERR, msg)
    
# Print version in syslog for easier troubleshooting
VERSION = "0.3"
loginf("IDOKEP version %s" % VERSION)


# ==============================================================================
# IDOKEP
# ==============================================================================

class IDOKEP(StdRESTful):
    """Upload data to IDOKEP - 
    https://pro.idokep.hu

    To enable this module, add the following to weewx.conf:

    [StdRESTful]
        [[IDOKEP]]
            enable   = True
            username = your IDOKEP username
            password = your IDOKEP password
            log_success = True
            log_failure = True
            skip_upload = False
            station_type = WS23XX

    https://pro.idokep.hu

    URL=https://pro.idokep.hu/sendws.php?
    PARAMETERS:
        user=username
        pass=password
        hom=$v{To} // Temperature outdoor (%.1f C)
        rh=$v{RHo} // Relative humidity outdoor (%d C)
        szelirany=$v{DIR0} // Wind direction (%.0f)
        szelero=$v{WS} // (%.1f m/s)
        p=$v{RP} // Relative pressure (%.1f hPa)
        csap=$v{R24h} // Rain 24h (%.1f mm)
        csap1h=$v{R1h} //Rain 1h (%.1f mm)
        ev=$year
        ho=$mon
        nap=$mday
        ora=$hour
        perc=$min
        mp=$sec
        tipus=WS23xx
    
    """

    def __init__(self, engine, config_dict):
        super(IDOKEP, self).__init__(engine, config_dict)

        site_dict = get_site_dict(config_dict, 'IDOKEP', 'username', 'password')
        if site_dict is None:
            logerr("IDOKEP: Data will not be posted. Check weewx.conf for missing parameters")
            return
        
        site_dict.setdefault('station_type', engine.stn_info.hardware)        

        site_dict['manager_dict'] = weewx.manager.get_manager_dict_from_config(
            config_dict, 'wx_binding')

        self.archive_queue = queue.Queue()
        self.archive_thread = IDOKEPThread(self.archive_queue, **site_dict)
        self.archive_thread.start()
        self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)
        loginf("IDOKEP: Data will be uploaded for user %s" % site_dict['username'])

    def new_archive_record(self, event):
        self.archive_queue.put(event.record)


class IDOKEPThread(RESTThread):
    _SERVER_URL = 'https://pro.idokep.hu/sendws.php'
    _FORMATS = {'barometer'   : '%.1f',
                'outTemp'     : '%.1f',
                'outHumidity' : '%.0f',
                'windSpeed'   : '%.1f',
                'windDir'     : '%.0f',
                'hourRain'    : '%.2f',
                'dayRain'     : '%.2f'}

    def __init__(self, q, username, password,
                 manager_dict, station_type='WS23XX', server_url=_SERVER_URL,
                 post_interval=300, max_backlog=six.MAXSIZE, stale=None,
                 log_success=True, log_failure=True,
                 timeout=10, max_tries=3, retry_wait=5,
                 retry_login=3600, retry_certificate=3600, skip_upload=False):
        """Initialize an instances of IDOKEPThread.

        Parameters specific to this class:

          username: IDOKEP user name

          password: IDOKEP password

          manager_dict: A dictionary holding the database manager
          information. It will be used to open a connection to the archive 
          database.

          station_type: weather station type
        
          server_url: URL of the server
          Default is the IDOKEP Pro site

        """
        super(IDOKEPThread, self).__init__(q,
                                           protocol_name='IDOKEP',
                                           manager_dict=manager_dict,
                                           post_interval=post_interval,
                                           max_backlog=max_backlog,
                                           stale=stale,
                                           log_success=log_success,
                                           log_failure=log_failure,
                                           timeout=timeout,
                                           max_tries=max_tries,
                                           retry_wait=retry_wait,
                                           retry_login=retry_login,
                                           retry_certificate=retry_certificate,
                                           skip_upload=skip_upload)
        self.username = username
        self.password = password        
        self.station_type = station_type
        self.server_url = server_url
        self.skip_upload = to_bool(skip_upload)

    def get_record(self, record, dbmanager):
        # Have my superclass process the record first.
        record = super(IDOKEPThread, self).get_record(record, dbmanager)
        return record

    def format_url(self, in_record):    

        record = weewx.units.to_METRICWX(in_record)

        time_tt = time.gmtime(record['dateTime'])
        # assemble an array of values in the proper order
        values = ["{0}={1}".format("user",self.username)]
        values.append("{0}={1}".format("pass",self.password))
        time_tt = time.localtime(record['dateTime'])
        values.append("{0}={1}".format("ev",time.strftime("%Y", time_tt)))
        values.append("{0}={1}".format("ho",time.strftime("%m", time_tt)))
        values.append("{0}={1}".format("nap",time.strftime("%d", time_tt)))
        values.append("{0}={1}".format("ora",time.strftime("%H", time_tt)))
        values.append("{0}={1}".format("perc",time.strftime("%M", time_tt)))
        values.append("{0}={1}".format("mp",time.strftime("%S", time_tt)))
        values.append("{0}={1}".format("hom",self._format(record, 'outTemp'))) # C
        values.append("{0}={1}".format("rh",self._format(record, 'outHumidity'))) # %
        values.append("{0}={1}".format("szelirany",self._format(record, 'windDir')))
        values.append("{0}={1}".format("szelero",self._format(record, 'windSpeed'))) # m/s
        values.append("{0}={1}".format("szellokes",self._format(record, 'windGust'))) # m/s
        values.append("{0}={1}".format("p",self._format(record, 'barometer'))) # hPa
        values.append("{0}={1}".format("csap",self._format(record, 'rain24'))) # mm
        values.append("{0}={1}".format("csap1h",self._format(record, 'hourRain'))) # mm
        values.append("{0}={1}".format("tipus",self.station_type))
            
        valstr = '&'.join(values)
        url = self.server_url + '?' + valstr

        if weewx.debug >= 2:
            # show the url in the logs for debug, but mask any credentials
            logdbg('IDOKEP: url: %s', url.replace(self.password, 'XXX'))

        return url

    def _format(self, record, label):
        if label in record and record[label] is not None:
            if label in self._FORMATS:
                return self._FORMATS[label] % record[label]
            return str(record[label])
        return ''

    def check_response(self, response):
        error = True
        for line in response:
            if line.find('sz!'):
                error=False                
        if error:
            logerr("Server returned '%s'" % ', '.join(response))
        loginf("IDOKEP: Upload response received: %s" % response)

    def process_record(self, record, archive):
        r = self.get_record(record, archive)
        url = self.format_url(r)
        if self.skip_upload:
            loginf("IDOKEP: skipping upload")
            return
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "weewx/%s" % weewx.__version__)
        self.post_with_retries(req)
        loginf("IDOKEP: Upload request sent: %s" % req)
