#
# Copyright 2018 3liz
# Author David Marteau
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

""" Qgis server request adapters

    Embedded qgis server in a 0MQ worker 

    see: 
        - https://qgis.org/pyqgis/master/server/QgsBufferServerResponse.html
        - https://qgis.org/pyqgis/master/server/QgsBufferServerRequest.html
"""
import os
import sys
import logging
import traceback

from typing import Dict

from qgis.PyQt.QtCore import QBuffer, QIODevice, QByteArray
from qgis.server import (QgsServerRequest,
                         QgsServerResponse)

from .zeromq.worker import RequestHandler, run_worker
from .qgscache.cachemanager import cacheservice, StrictCheckingError

from .config  import confservice
from .plugins import load_plugins

LOGGER = logging.getLogger('SRVLOG')


class Request(QgsServerRequest):

    def __init__(self, handler: RequestHandler ) -> None:
        """ Create a new QgsServerRequest from zmq handler request
        """
        req = handler.request
        
        # Recreate URL
        location = req.headers.get('X-Proxy-Location',"")
        location += '?'+req.query.lstrip('?')

        self._data = req.data

        super().__init__(location, method={
            'GET' : QgsServerRequest.GetMethod,
            'PUT' : QgsServerRequest.PutMethod,
            'POST': QgsServerRequest.PostMethod,
            }[req.method], headers=req.headers)
       
    def data(self) -> QByteArray: 
        """ Return post/put data a QByteArray
        """
        # Make sure that data is valid
        return QByteArray(self._data) if self._data else QByteArray()


class Response(QgsServerResponse):
    """ Adaptor to handler response

        The data is written at 'flush()' call.
    """

    def __init__(self, handler: RequestHandler ) -> None:
        super().__init__()
        self._handler = handler
        self._buffer = QBuffer()
        self._buffer.open(QIODevice.ReadWrite)
        self._numbytes = 0
        self._finish   = False

    def setStatusCode(self, code: int) -> None:
        if not self._handler.header_written:
            self._handler.status_code = code
        else:
            LOGGER.error("Cannot set status code after header written")

    def statusCode(self) -> int:
        return self._handler.status_code

    def finish(self) -> None:
        """ Terminate the request
        """
        self._finish = True
        self.flush()

    def flush(self) -> None:
        """ Write the data to the handler buffer 
            and flush the socket

            Headers will be written at the first call to flush()
        """
        try:
            self._buffer.seek(0)
            bytesAvail = self._buffer.bytesAvailable()
            LOGGER.debug("%s: Flushing response data: (%d bytes)",self._handler.identity, bytesAvail)
            if self._finish:
                self._handler.headers['Content-Length']=bytesAvail
            # Take care of the logic: if finish and not handler.header_written then there is no
            # chunk following
            send_more = not self._finish or self._handler.header_written
            if bytesAvail:
                self._handler.send( bytes(self._buffer.data()), send_more )
                self._buffer.buffer().clear()
            # push the sentinel
            if send_more and self._finish:
                self._handler.send( b'', False )
        except:
            LOGGER.error("Caught Exception (worker: %s, msg: %s):\n%s",
                          self._handler.identity, self._handler.msgid,
                          traceback.format_exc())
            del self._handler.headers['Content-Type']
            self.sendError(500, "Internal server error")

    def header(self, key: str) -> str:
        return self._handler.headers.get(key)

    def headers(self) -> Dict[str,str]:
        """ Return headers as dict
        """
        return self._handler.headers
        
    def io(self) -> QIODevice:
        return self._buffer

    def data(self) -> QByteArray:
        """ Return buffer data
        """
        return self._buffer.data()

    def setHeader(self, key: str, value: str) -> None:
        if not self._handler.header_written:
            self._handler.headers[key] = value
        else:
            LOGGER.error("Cannot set header after header written")

    def removeHeader(self, key: str) -> None:
        self._handler.headers.pop(key,None)
   
    def sendError(self, code: int, message: str=None) -> None:
        try:
            if not self._handler.header_written:
                LOGGER.error("%s (%s)", message, code)
                self._handler.status_code = code
                self._handler.send(bytes(str(message).encode('ascii')))
                self._finish = True
            else:
                LOGGER.error("Cannot set error after header written")
        except:
            lOGGER.critical("Unrecoverable exception:\n%s", traceback.format_exc())


    def _clearHeaders(self) -> None:
        """ Clear headers set so far
        """
        self._handler.headers = {}
 
    def clear(self) -> None:
        self._clearHeaders()
        self.truncate()

    def headersSent(self) -> bool:
        return self._handler.header_written

    def truncate(self) -> None:
        """ Truncate buffer
        """
        self._buffer.seek(0)
        self._buffer.buffer().clear()


class QgsRequestHandler(RequestHandler):

    @classmethod
    def init_server(cls) -> None:
        if not hasattr(cls, 'qgis_server' ):
            from .utils.qgis import init_qgis_server

            # Enable qgis server verbosity
            if LOGGER.isEnabledFor(logging.DEBUG):
                os.environ['QGIS_SERVER_LOG_LEVEL'] = '0'
                os.environ['QGIS_DEBUG'] = '1'

            LOGGER.debug("Initializing qgis server")
            qgsserver = init_qgis_server( enable_processing=False, 
                                          logger=LOGGER, 
                                          verbose=LOGGER.level<=logging.DEBUG)

            load_plugins(qgsserver.serverInterface()) 

            setattr(cls, 'qgis_server' , qgsserver )

    @staticmethod
    def run( router: str, identity: str="", broadcastaddr: str=None) -> None:
        """ Run qgis server worker loop
        """
        QgsRequestHandler.init_server()

        run_worker(router, QgsRequestHandler, identity=bytes(identity.encode('ascii')),
                   broadcastaddr=broadcastaddr)

    def handle_message(self) -> None:
        """ Override this method to handle_messages
        """
        project_location = self.request.headers.pop('X-Map-Location')

        request  = Request(self)
        response = Response(self)

        iface = self.qgis_server.serverInterface()
        try:
            LOGGER.debug("Handling request: %s", self.msgid)
            project, updated = cacheservice.lookup(project_location)
            config_path = project.fileName()
            if updated: 
               # Needed to cleanup cache capabilities cache
               LOGGER.debug("Cleaning config cache entry %s", config_path)
               iface.removeConfigCacheEntry(config_path)
        except StrictCheckingError:
            response.sendError(422,"Invalid layers for project '%s' - strict mode on" % project_location)
        except FileNotFoundError:
            response.sendError(404,"Project '%s' not found" % project_location)
        else:
            # See https://github.com/qgis/QGIS/pull/9773
            iface.setConfigFilePath(config_path)
            self.qgis_server.handleRequest(request, response, project=project)


def main():
    """ Run as command line interface
    """
    import os
    import sys
    import argparse
    from .zeromq.worker import run_worker
    from .version import __description__, __version__
    from .config  import (confservice, read_config_dict, validate_config_path)
    from .logger import setup_log_handler

    parser = argparse.ArgumentParser(description='Qgis Server Worker')
    parser.add_argument('--host'     , metavar="host"   , default='tcp://localhost'   , help="router host")   
    parser.add_argument('--router'   , metavar='address', default='tcp://{host}:18080', help="router address")
    parser.add_argument('--broadcast', metavar='address', default='tcp://{host}:18090', help="broadcast address")
    parser.add_argument('--identity' , default="", help="Set worker identity")
    parser.add_argument('--rootdir'  , default=confservice['cache']['rootdir'], metavar='PATH', help='Path to qgis projects')
    parser.add_argument('--version'  , action='store_true', default=False, help="Return version number and exit")
    parser.add_argument('--logging'  , choices=['debug', 'info', 'warning', 'error'], 
            default=confservice['logging']['level'].lower(), help="set log level")

    args = parser.parse_args()

    def print_version():
        program = os.path.basename(sys.argv[0])
        print("{name} {version}".format(name=program, version=__version__), file=sys.stderr)

    if args.version:
        print_version()
        sys.exit(1)

    # read configuration dict
    read_config_dict({
        'logging':{ 'level': args.logging.upper() },
        'cache'  :{ 'rootdir': args.rootdir },
    })

    print_version()

    validate_config_path('cache','rootdir')

    setup_log_handler(args.logging)
    print("Log level set to {}\n".format(logging.getLevelName(LOGGER.level)), file=sys.stderr)

    LOGGER.setLevel(getattr(logging, args.logging.upper()))

    broadcastaddr = args.broadcast.format(host=args.host)

    QgsRequestHandler.run(args.router.format(host=args.host), identity=args.identity,
                          broadcastaddr=broadcastaddr)

    print("Qgis worker terminated", file=sys.stderr)



