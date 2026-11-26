"""Test the real feedback HTTP/Mongo workflow in an empty disposable *_test database.

Run with python -m tests.integration_feedback, MONGO_URI and MONGO_DB set.
Only synthetic predictions are used. No provider key or SQL connection is needed.
"""
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import json
import os
import socket
from threading import Thread
import unittest
from uuid import uuid4

import uvicorn

from backend.api import app
from backend.live import LiveArrivals, MongoLiveCache
from backend.storage import initialize_mongo, mongo_database


class RiderFeedbackIntegration(unittest.TestCase):
    def test_private_crud_public_counts_and_expiry(self):
        self.assertTrue(os.environ.get('MONGO_DB', '').endswith('_test'))
        client, db = mongo_database()
        self.addCleanup(client.close)
        self.assertEqual(db.list_collection_names(), [], 'Use an empty disposable database.')
        initialize_mongo(db)
        self.assertTrue(any(i.get('expireAfterSeconds') == 0 for i in db.arrival_feedback.list_indexes()))
        now = datetime.now(timezone.utc)
        stop = '12345'
        bus = {'eta': now + timedelta(minutes=7), 'rank': 1,
               'destinationCode': '90000', 'originCode': '11111', 'visitNumber': 1}
        obs = {'meta': {'stopCode': stop, 'serviceNo': '99', 'operator': 'TEST'}, 'arrivals':[bus]}
        db.arrival_live.insert_one({'_id': stop, 'capturedAt': now, 'freshUntil': now + timedelta(minutes=2),
                                    'observations': [obs]})
        app.state.mongo = db
        app.state.live = LiveArrivals(MongoLiveCache(db), None)
        a, b = str(uuid4()), str(uuid4())
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            sock.listen()
            port = sock.getsockname()[1]
            config = uvicorn.Config(app, lifespan='off', log_level='error', access_log=False)
            server = uvicorn.Server(config)
            thread = Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
            thread.start()

            def request(method, path, key=None, issue=None, expected=200):
                headers = {'Content-Type':'application/json'}
                if key is not None:
                    headers['X-Journey-Key'] = key
                conn = HTTPConnection('127.0.0.1', port, timeout=5)
                try:
                    conn.request(method, path, json.dumps({'issue':issue}) if issue else None, headers)
                    response = conn.getresponse()
                    data = json.loads(response.read())
                    self.assertEqual(response.status, expected, data)
                    return data
                finally:
                    conn.close()
            try:
                listing = '/feedback/' + stop
                arrival = request('GET', '/arrivals/'+stop)['observations'][0]['arrivals'][0]
                arrival_key = arrival['feedbackKey']
                path = listing + '/' + arrival_key
                self.assertEqual(request('GET', listing), {'reports': {}})
                request('PUT', path, issue='full', expected=400)
                request('PUT', path, key=a, issue='invalid', expected=422)
                request('PUT', listing+'/'+('0'*64), key=a, issue='full', expected=409)
                self.assertEqual(request('PUT', path, a, 'full')['mine'], 'full')
                self.assertEqual(request('PUT', path, a, 'full')['counts']['full'], 1)
                self.assertEqual(request('PUT', path, b, 'full')['counts']['full'], 2)
                self.assertEqual(request('PUT', path, a, 'delayed')['counts'], {'full':1,'delayed':1,'skipped':0})
                self.assertIsNone(request('GET', listing)['reports'][arrival_key]['mine'])
                self.assertEqual(request('GET', listing, b)['reports'][arrival_key]['mine'], 'full')
                self.assertEqual(request('DELETE', path, a)['counts'], {'full':1,'delayed':0,'skipped':0})
                self.assertEqual(request('DELETE', path, a)['counts']['full'], 1)
                # An ETA revision cannot transfer a previous report to the next displayed time.
                db.arrival_live.update_one({'_id': stop}, {'$set':{'observations.0.arrivals.0.eta':
                                            now + timedelta(minutes=8)}})
                revised = request('GET', '/arrivals/'+stop)['observations'][0]['arrivals'][0]['feedbackKey']
                self.assertNotEqual(revised, arrival_key)
                request('PUT', path, a, 'skipped', expected=409)
                self.assertNotIn(revised, request('GET', listing)['reports'])
                db.arrival_feedback.update_many({}, {'$set':{'expiresAt':now-timedelta(seconds=1)}})
                self.assertEqual(request('GET', listing), {'reports':{}})
            finally:
                server.should_exit = True
                thread.join(10)


if __name__ == '__main__':
    unittest.main()
