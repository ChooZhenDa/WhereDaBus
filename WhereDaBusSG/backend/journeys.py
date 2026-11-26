"""Private, browser-owned saved journeys. Only a digest of the browser key is stored."""
from contextlib import contextmanager
from hashlib import sha256
import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from psycopg.errors import ForeignKeyViolation
from psycopg.rows import dict_row
from pydantic import BaseModel, StringConstraints

from .storage import sql_connection


router = APIRouter(prefix='/journeys')
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
StopCode = Annotated[str, StringConstraints(pattern=r'^[0-9]{5}$')]


class JourneyLabel(BaseModel):
    label: Label


class JourneyCreate(JourneyLabel):
    origin: StopCode
    destination: StopCode


def browser_owner(response: Response, key: Annotated[str | None, Header(alias='X-Journey-Key')] = None):
    try:
        token = UUID(key or '')
        if token.version != 4:
            raise ValueError()
    except ValueError:
        raise HTTPException(400, 'A valid browser journey key is required.') from None
    response.headers['Cache-Control'] = 'no-store'
    return sha256(token.bytes).digest()


Owner = Annotated[bytes, Depends(browser_owner)]
SELECT_JOURNEYS = """
    SELECT j.journey_id AS id, j.label, j.origin, j.destination,
           a.description AS "originName", b.description AS "destinationName"
      FROM transport.saved_journeys j
      JOIN transport.bus_stops a ON a.stop_code = j.origin
      JOIN transport.bus_stops b ON b.stop_code = j.destination
     WHERE j.owner_hash = %s
"""


@contextmanager
def journey_cursor():
    if not os.getenv('SQL_DSN', '').strip():
        raise HTTPException(503, 'Saved journeys require a PostgreSQL connection.')
    with sql_connection() as connection:
        connection.execute("SET LOCAL statement_timeout = '5s'")
        with connection.cursor(row_factory=dict_row) as cursor:
            yield cursor


def read_journey(cursor, owner, journey_id):
    cursor.execute(SELECT_JOURNEYS + ' AND j.journey_id = %s', (owner, journey_id))
    return cursor.fetchone()


@router.get('')
def list_journeys(owner: Owner):
    with journey_cursor() as cursor:
        cursor.execute(SELECT_JOURNEYS + ' ORDER BY j.created_at DESC, j.journey_id', (owner,))
        return cursor.fetchall()


@router.post('', status_code=201)
def create_journey(body: JourneyCreate, owner: Owner):
    if body.origin == body.destination:
        raise HTTPException(422, 'Choose different boarding and destination stops.')
    with journey_cursor() as cursor:
        try:
            cursor.execute("""
                INSERT INTO transport.saved_journeys (owner_hash, label, origin, destination)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (owner_hash, origin, destination) DO NOTHING
                RETURNING journey_id
            """, (owner, body.label, body.origin, body.destination))
        except ForeignKeyViolation:
            raise HTTPException(422, 'One of the stops is missing from the imported network.') from None
        created = cursor.fetchone()
        if created is None:
            raise HTTPException(409, 'This journey is already in your saved journeys.')
        return read_journey(cursor, owner, created['journey_id'])


@router.patch('/{journey_id}')
def rename_journey(journey_id: UUID, body: JourneyLabel, owner: Owner):
    with journey_cursor() as cursor:
        cursor.execute("""
            UPDATE transport.saved_journeys SET label = %s
             WHERE owner_hash = %s AND journey_id = %s
        """, (body.label, owner, journey_id))
        if cursor.rowcount == 0:
            raise HTTPException(404, 'Saved journey not found.')
        return read_journey(cursor, owner, journey_id)


@router.delete('/{journey_id}', status_code=204)
def delete_journey(journey_id: UUID, owner: Owner):
    with journey_cursor() as cursor:
        cursor.execute('DELETE FROM transport.saved_journeys WHERE owner_hash = %s AND journey_id = %s',
                       (owner, journey_id))
        if cursor.rowcount == 0:
            raise HTTPException(404, 'Saved journey not found.')
    return Response(status_code=204, headers={'Cache-Control': 'no-store'})
