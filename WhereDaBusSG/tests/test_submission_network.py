import io
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from backend import submission_network as network


ROOT = Path(__file__).resolve().parents[1]
ROWS = {
    'bus_stops': b'01234\tRoad\tStop\t1.3\t103.8\tt\t2026-11-20 18:29:23+00\n',
    'bus_services': b'100\t1\tSBS\t\t01234\t\\N\t\tt\t2026-11-20 18:29:23+00\n',
    'bus_routes': b'100\t1\t1\t01234\t0.00\n',
}


def section(table):
    return (f'COPY transport.{table} ({network.COLUMNS[table]}) FROM stdin;\n'.encode()
            + ROWS[table] + b'\\.\n')


class SubmissionNetworkTests(unittest.TestCase):
    def test_packaged_dump_yields_only_the_three_complete_datasets(self):
        with (ROOT / 'data' / 'postgresql.sql').open('rb') as source:
            blocks = network.read_network(source)
        self.assertEqual({table: count for table, (_, count) in blocks.items()},
                         {'bus_stops': 5209, 'bus_services': 804, 'bus_routes': 26946})

    def test_skip_if_catalogue_populated(self):
        connection = MagicMock()
        cur = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = (True,)
        with patch.object(network, 'sql_connection', return_value=connection):
            self.assertEqual(network.seed_network(io.BytesIO()), {'status': 'kept-existing-network'})
        statements = [call.args[0] for call in cur.execute.call_args_list]
        self.assertEqual(len(statements), 2)
        self.assertIn('pg_advisory_xact_lock(51003)', statements[0])
        self.assertIn('transport.bus_services', statements[1])
        self.assertIn('transport.bus_routes', statements[1])
        cur.copy.assert_not_called()

    def test_out_of_order_dump_preserves_existing_stops_and_private_tables(self):
        dump = (b'CREATE SCHEMA transport;\nDROP TABLE transport.saved_journeys;\n'
                + section('bus_routes') + section('bus_services') + section('bus_stops')
                + b'COPY transport.users (user_id) FROM stdin;\nunused\n\\.\n')
        connection = MagicMock()
        cur = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = (False,)
        with patch.object(network, 'sql_connection', return_value=connection):
            result = network.seed_network(io.BytesIO(dump))
        self.assertEqual(result['routeOccurrences'], 1)
        writes = cur.copy.return_value.__enter__.return_value.write.call_args_list
        self.assertEqual([call.args[0] for call in writes], list(ROWS.values()))
        copy_targets = [call.args[0].split()[1] for call in cur.copy.call_args_list]
        self.assertEqual(copy_targets, ['pg_temp.submission_stops', 'transport.bus_services', 'transport.bus_routes'])
        statements = '\n'.join(call.args[0] for call in cur.execute.call_args_list)
        self.assertIn('ON CONFLICT (stop_code) DO NOTHING', statements)
        self.assertNotIn('transport.saved_journeys', statements)
        self.assertNotIn('transport.users', statements)
        self.assertEqual(connection.__exit__.call_args.args, (None, None, None))

    def test_incomplete_or_duplicate_inputs_abort_the_transaction_before_writes(self):
        stops = section('bus_stops')
        for dump in [stops, stops + stops, stops[:-3]]:
            with self.subTest(dump=dump):
                connection = MagicMock()
                cur = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
                cur.fetchone.return_value = (False,)
                with patch.object(network, 'sql_connection', return_value=connection):
                    with self.assertRaises(ValueError):
                        network.seed_network(io.BytesIO(dump))
                cur.copy.assert_not_called()
                self.assertIs(connection.__exit__.call_args.args[0], ValueError)
