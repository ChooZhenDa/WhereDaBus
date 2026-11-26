"""Exercise the live HTTP endpoints in an empty, disposable SQL database ending in _test.

Run python -m tests.integration_saved_journeys with SQL_DSN set. Uses the application's
runtime dependencies only. The caller supplies and removes the isolated test database.
"""
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import socket
from threading import Thread
import unittest
from uuid import uuid4

import uvicorn

from backend.api import app
from backend.bootstrap import initialize_sql
from backend.storage import sql_connection


class JourneyIntegration(unittest.TestCase):
    def test_migrated_commute_and_private_crud(self):
        self.assertTrue(os.environ.get('SQL_DSN'), 'Set SQL_DSN to an empty test database.')
        sql = Path(__file__).resolve().parents[1] / 'sql'
        with sql_connection() as conn:
            self.assertTrue(conn.execute('SELECT current_database()').fetchone()[0].endswith('_test'))
            self.assertIsNone(conn.execute("SELECT to_regnamespace('transport')").fetchone()[0])
            for script in ('001_schema.sql', '002_queries.sql'):
                conn.execute((sql / script).read_text(encoding='utf8'))
            conn.execute('CREATE TABLE transport.schema_version(version int PRIMARY KEY, applied_at timestamptz DEFAULT now())')
            conn.execute('INSERT INTO transport.schema_version(version) VALUES (2)')
            conn.execute("""INSERT INTO transport.bus_stops (stop_code,road_name,description,latitude,longitude)
                            VALUES ('65069','Board Road','Blk 190C',1.37,103.90),
                                   ('67131','Alight Road','Bakau Station',1.38,103.91)""")

        self.assertEqual(initialize_sql(), 'upgraded')
        self.assertEqual(initialize_sql(), 'ready')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            sock.listen()
            server = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_level='error', access_log=False))
            thread = Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
            thread.start()
            port = sock.getsockname()[1]
            owner, visitor = str(uuid4()), str(uuid4())

            def request(method, path, key, body=None):
                headers = {'Content-Type': 'application/json'}
                if key is not None:
                    headers['X-Journey-Key'] = key
                conn = HTTPConnection('127.0.0.1', port, timeout=10)
                try:
                    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
                    response = conn.getresponse()
                    data = response.read()
                    return response.status, json.loads(data) if data else None
                finally:
                    conn.close()

            try:
                self.assertEqual(request('GET', '/journeys', None)[0], 400)
                self.assertEqual(request('GET', '/journeys', owner), (200, []))
                payload = {'label': '  Morning commute  ', 'origin': '65069', 'destination': '67131'}
                status, saved = request('POST', '/journeys', owner, payload)
                self.assertEqual(status, 201, saved)
                self.assertEqual(saved['label'], 'Morning commute')
                self.assertEqual((saved['originName'], saved['destinationName']), ('Blk 190C', 'Bakau Station'))
                self.assertEqual(request('POST', '/journeys', owner, payload)[0], 409)
                self.assertEqual(request('POST', '/journeys', owner, {**payload, 'origin': '00000'})[0], 422)
                self.assertEqual(request('GET', '/journeys', owner), (200, [saved]))
                path = '/journeys/' + saved['id']
                self.assertEqual(request('GET', '/journeys', visitor), (200, []))
                self.assertEqual(request('PATCH', path, visitor, {'label': 'Private'})[0], 404)
                self.assertEqual(request('DELETE', path, visitor)[0], 404)
                self.assertEqual(request('PATCH', path, owner, {'label': '  '})[0], 422)
                status, changed = request('PATCH', path, owner, {'label': 'Campus'})
                self.assertEqual((status, changed['label']), (200, 'Campus'))
                self.assertEqual(request('GET', '/journeys', owner), (200, [changed]))
                self.assertEqual(request('DELETE', path, owner), (204, None))
                self.assertEqual(request('GET', '/journeys', owner), (200, []))
            finally:
                server.should_exit = True
                thread.join(10)


if __name__ == '__main__':
    unittest.main()
