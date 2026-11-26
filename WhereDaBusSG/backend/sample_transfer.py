"""Portable, lossless transfer of WhereDaBus historical observations."""
import argparse
import hashlib
import json
import os
import struct
import tempfile
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from bson import BSON

FORMAT = 'wheredabus-observations-bson-v1'
DATA = 'arrival_observations.bson'
BATCH = 500


def export_samples(collection, path):
    cutoff = datetime.now(timezone.utc)
    digest = hashlib.sha256()
    count = 0
    first = last = None
    with ZipFile(path, 'x', compression=ZIP_DEFLATED, allowZip64=True) as archive:
        with archive.open(DATA, 'w', force_zip64=True) as stream:
            cursor = collection.find({'observedAt': {'$lt': cutoff}}).sort('observedAt', 1)
            for row in cursor:
                raw = BSON.encode(row)
                stream.write(raw)
                digest.update(raw)
                count += 1
                first = first or row['observedAt']
                last = row['observedAt']
        if count == 0:
            raise ValueError('No historical observations found in this installation.')
        info = dict(format=FORMAT, count=count, cutoffUtc=cutoff.isoformat(),
                    firstUtc=first.isoformat(), lastUtc=last.isoformat(), sha256=digest.hexdigest())
        archive.writestr('manifest.json', json.dumps(info))
    return info


def rows_in(archive, digest=None):
    with archive.open(DATA) as stream:
        while header := stream.read(4):
            if len(header) != 4:
                raise ValueError('Incomplete BSON header in sample backup.')
            size, = struct.unpack('<i', header)
            if not 5 <= size <= 16 * 1024 * 1024:
                raise ValueError('Invalid BSON record length in sample backup.')
            raw = header + stream.read(size - 4)
            if len(raw) != size:
                raise ValueError('Incomplete observation in sample backup.')
            if digest is not None:
                digest.update(raw)
            row = BSON(raw).decode()
            if ('_id' not in row or not isinstance(row.get('observedAt'), datetime)
                    or not isinstance(row.get('meta'), dict) or not isinstance(row.get('arrivals'), list)
                    or not row.get('logicalKey')):
                raise ValueError('Backup contains a record outside the WhereDaBus observations format.')
            yield row


def verified_manifest(archive):
    if sorted(archive.namelist()) != sorted((DATA, 'manifest.json')):
        raise ValueError('Invalid sample archive contents.')
    info = json.loads(archive.read('manifest.json'))
    if info.get('format') != FORMAT or not isinstance(info.get('count'), int) or info['count'] < 1:
        raise ValueError('Unrecognized or empty WhereDaBus sample archive.')
    digest = hashlib.sha256()
    count = sum(1 for _ in rows_in(archive, digest))
    if count != info['count'] or digest.hexdigest() != info['sha256']:
        raise ValueError('Sample backup failed count or SHA-256 verification.')
    return info


def restore_samples(collection, archive):
    # Validate the entire archive before the first insert. Preserve original BSON
    # IDs and times, including distinct documents representing a replayed poll.
    info = verified_manifest(archive)
    inserted = skipped = expired = 0
    expiry = collection.options().get('expireAfterSeconds')
    if collection.options().get('timeseries', {}).get('timeField') != 'observedAt':
        raise ValueError('Destination must be the initialized WhereDaBus time-series collection.')
    now = datetime.now(timezone.utc).timestamp()
    rows = rows_in(archive)
    while batch := list(islice(rows, BATCH)):
        eligible = [row for row in batch if not isinstance(expiry, (int, float))
                    or row['observedAt'].replace(tzinfo=timezone.utc).timestamp() + expiry > now]
        expired += len(batch) - len(eligible)
        if not eligible:
            continue
        times = [row['observedAt'] for row in eligible]
        query = {'observedAt': {'$gte': min(times), '$lte': max(times)},
                 '_id': {'$in': [row['_id'] for row in eligible]}}
        present = {row['_id'] for row in collection.find(query, {'_id': 1})}
        missing = [row for row in eligible if row['_id'] not in present]
        skipped += len(eligible) - len(missing)
        if missing:
            collection.insert_many(missing, ordered=True)
            inserted += len(missing)
    return dict(verified=info['count'], inserted=inserted, alreadyPresent=skipped,
                outsideRetention=expired, sha256=info['sha256'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['export', 'verify', 'restore'])
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    if args.action == 'verify':
        with ZipFile(args.archive) as archive:
            print(json.dumps(verified_manifest(archive)))
        return
    from backend.storage import mongo_database, sql_connection
    client, db = mongo_database()
    try:
        if args.action == 'export':
            # Export to a sibling temporary directory; incomplete archives never
            # replace a finished backup. This command runs inside the container.
            with tempfile.TemporaryDirectory(dir=args.archive.parent) as temp:
                staging = Path(temp) / 'samples.zip'
                result = export_samples(db.arrival_observations, staging)
                with ZipFile(staging) as archive:
                    verified_manifest(archive)
                os.link(staging, args.archive)
        else:
            # Time-series collections cannot enforce uniqueness on individual
            # measurement IDs. Serialize restores to the same installation.
            with sql_connection() as conn, ZipFile(args.archive) as archive:
                locked = conn.execute('SELECT pg_try_advisory_xact_lock(829615042)').fetchone()[0]
                if not locked:
                    raise SystemExit('Another sample restore is running. Retry when it finishes.')
                result = restore_samples(db.arrival_observations, archive)
        print(json.dumps(result))
    finally:
        client.close()


if __name__ == '__main__':
    main()
