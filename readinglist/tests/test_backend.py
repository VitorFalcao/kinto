import mock
import redis
import six
import time

from readinglist.backend import BackendBase, exceptions
from readinglist.backend.simpleredis import (
    Redis, load_from_config as load_redis_from_config
)
from readinglist.backend.memory import Memory
from readinglist import utils

from .support import unittest, ThreadMixin


class BackendBaseTest(unittest.TestCase):
    def setUp(self):
        self.backend = BackendBase()

    def test_default_generator(self):
        self.assertEqual(type(self.backend.id_generator()), six.text_type)

    def test_custom_generator(self):
        def l(x):
            return x
        backend = BackendBase(id_generator=l)
        self.assertEqual(backend.id_generator, l)

    def test_mandatory_overrides(self):
        calls = [
            (self.backend.flush,),
            (self.backend.ping,),
            (self.backend.collection_timestamp, '', ''),
            (self.backend.create, '', '', {}),
            (self.backend.get, '', '', ''),
            (self.backend.update, '', '', '', {}),
            (self.backend.delete, '', '', ''),
            (self.backend.get_all, '', ''),
        ]
        for call in calls:
            self.assertRaises(NotImplementedError, *call)


class TestResource(object):
    id_field = "id"
    modified_field = "last_modified"


class BaseTestBackend(object):
    def setUp(self):
        super(BaseTestBackend, self).setUp()
        self.resource = TestResource()
        self.record = {'foo': 'bar'}
        self.user_id = 1234

    def tearDown(self):
        super(BaseTestBackend, self).tearDown()
        self.backend.flush()

    def test_create_adds_the_record_id(self):
        record = self.backend.create(self.resource, self.user_id, self.record)
        self.assertIsNotNone(record['id'])

    def test_create_works_as_expected(self):
        stored = self.backend.create(self.resource, self.user_id, self.record)
        retrieved = self.backend.get(self.resource, self.user_id, stored['id'])
        self.assertEquals(retrieved, stored)

    def test_create_copies_the_record_before_modifying_it(self):
        self.backend.create(self.resource, self.user_id, self.record)
        self.assertEquals(self.record.get('id'), None)

    def test_get_raise_on_record_not_found(self):
        self.assertRaises(
            exceptions.RecordNotFoundError,
            self.backend.get,
            self.resource,
            self.user_id,
            1234  # This record id doesn't exist.
        )

    def test_update_creates_a_new_record_when_needed(self):
        self.assertRaises(
            exceptions.RecordNotFoundError,
            self.backend.get,
            self.resource,
            self.user_id,
            1234  # This record id doesn't exist.
        )
        record = self.backend.update(self.resource, self.user_id, 1234,
                                     self.record)
        retrieved = self.backend.get(self.resource, self.user_id, 1234)
        self.assertEquals(retrieved, record)

    def test_update_overwrites_record_id(self):
        self.record['id'] = 4567
        self.backend.update(self.resource, self.user_id, 1234, self.record)
        retrieved = self.backend.get(self.resource, self.user_id, 1234)
        self.assertEquals(retrieved['id'], 1234)

    def test_delete_works_properly(self):
        stored = self.backend.create(self.resource, self.user_id, self.record)
        self.backend.delete(self.resource, self.user_id, stored['id'])
        self.assertRaises(
            exceptions.RecordNotFoundError,
            self.backend.get,
            self.resource, self.user_id, stored['id']  # Shouldn't exist.
        )

    def test_delete_raise_when_unknown(self):
        self.assertRaises(
            exceptions.RecordNotFoundError,
            self.backend.delete,
            self.resource, self.user_id, 1234
        )

    def test_get_all_return_all_values(self):
        for x in range(10):
            record = dict(self.record)
            record["number"] = x
            self.backend.create(self.resource, self.user_id, record)

        records, total_records = self.backend.get_all(self.resource,
                                                      self.user_id)
        self.assertEquals(len(records), 10)
        self.assertEquals(len(records), total_records)

    def test_ping_returns_true_when_working(self):
        self.assertTrue(self.backend.ping())

    def test_get_all_handle_limit(self):
        for x in range(10):
            record = dict(self.record)
            record["number"] = x
            self.backend.create(self.resource, self.user_id, record)

        records, total_records = self.backend.get_all(self.resource,
                                                      self.user_id,
                                                      limit=2)
        self.assertEqual(total_records, 10)
        self.assertEqual(len(records), 2)

    def test_get_all_handle_a_pagination_rules(self):
        for x in range(10):
            record = dict(self.record)
            record['id'] = x
            record["number"] = x % 3
            self.backend.create(self.resource, self.user_id, record)

        records, total_records = self.backend.get_all(
            self.resource, self.user_id, pagination_rules=[
                [('number', 1, utils.COMPARISON.GT)]
            ])
        self.assertEqual(total_records, 10)
        self.assertEqual(len(records), 3)

    def test_get_all_handle_all_pagination_rules(self):
        for x in range(10):
            record = dict(self.record)
            record["number"] = x % 3
            last_record = self.backend.create(self.resource, self.user_id,
                                              record)

        records, total_records = self.backend.get_all(
            self.resource, self.user_id, pagination_rules=[
                [('number', 1, utils.COMPARISON.GT)],
                [('id', last_record['id'], utils.COMPARISON.EQ)]

            ])
        self.assertEqual(total_records, 10)
        self.assertEqual(len(records), 4)


class TimestampsTest(object):
    def test_timestamp_are_incremented_on_create(self):
        self.backend.create(self.resource, self.user_id, self.record)  # init
        before = self.backend.collection_timestamp(self.resource, self.user_id)
        self.backend.create(self.resource, self.user_id, self.record)
        after = self.backend.collection_timestamp(self.resource, self.user_id)
        self.assertTrue(before < after)

    def test_timestamp_are_incremented_on_update(self):
        stored = self.backend.create(self.resource, self.user_id, self.record)
        _id = stored['id']
        before = self.backend.collection_timestamp(self.resource, self.user_id)
        self.backend.update(self.resource, self.user_id, _id, self.record)
        after = self.backend.collection_timestamp(self.resource, self.user_id)
        self.assertTrue(before < after)

    def test_timestamp_are_incremented_on_delete(self):
        stored = self.backend.create(self.resource, self.user_id, self.record)
        _id = stored['id']
        before = self.backend.collection_timestamp(self.resource, self.user_id)
        self.backend.delete(self.resource, self.user_id, _id)
        after = self.backend.collection_timestamp(self.resource, self.user_id)
        self.assertTrue(before < after)

    def test_timestamps_are_unique(self):
        obtained = []

        def create_item():
            for i in range(100):
                record = self.backend.create(
                    self.resource, self.user_id, self.record)
                obtained.append((record['last_modified'], record['id']))

        thread1 = self._create_thread(target=create_item)
        thread2 = self._create_thread(target=create_item)
        thread1.start()
        thread2.start()
        thread1.join()
        thread2.join()

        # With CPython (GIL), list appending is thread-safe
        self.assertEqual(len(obtained), 200)
        # No duplicated timestamps
        self.assertEqual(len(set(obtained)), len(obtained))

    def test_collection_timestamp_returns_now_when_not_found(self):
        before = utils.msec_time()
        time.sleep(0.001)  # 1 msec
        timestamp = self.backend.collection_timestamp(
            self.resource, self.user_id)
        time.sleep(0.001)  # 1 msec
        after = utils.msec_time()

        self.assertTrue(before < timestamp < after)

    def test_the_timestamp_are_based_on_real_time_milliseconds(self):
        before = utils.msec_time()
        time.sleep(0.001)  # 1 msec
        record = self.backend.create(self.resource, self.user_id, {})
        now = record['last_modified']
        time.sleep(0.001)  # 1 msec
        after = utils.msec_time()
        self.assertTrue(before < now < after)

    def test_timestamp_are_always_incremented_above_existing_value(self):
        # Create a record with normal clock
        record = self.backend.create(self.resource, self.user_id, {})
        current = record['last_modified']

        # Patch the clock to return a time in the past, before the big bang
        with mock.patch('readinglist.utils.msec_time') as time_mocked:
            time_mocked.return_value = -1

            record = self.backend.create(self.resource, self.user_id, {})
            after = record['last_modified']

        # Expect the last one to be based on the highest value
        self.assertTrue(0 < current < after)


class FieldsUnicityTest(object):
    def setUp(self):
        super(FieldsUnicityTest, self).setUp()
        self.resource.mapping = mock.MagicMock()
        self.resource.mapping.Options.unique_fields = ('phone',)

    def tearDown(self):
        super(FieldsUnicityTest, self).tearDown()
        self.resource.mapping.reset_mock()

    def create_record(self, record=None, user_id=None):
        record = record or {'phone': '0033677'}
        user_id = user_id or self.user_id
        return self.backend.create(self.resource, user_id, record)

    def test_cannot_insert_duplicate_field(self):
        self.create_record()
        self.assertRaises(exceptions.UnicityError,
                          self.create_record)

    def test_unicity_exception_gives_record_and_field(self):
        record = self.create_record()
        try:
            self.create_record()
        except exceptions.UnicityError as e:
            error = e
        self.assertEqual(error.field, 'phone')
        self.assertDictEqual(error.record, record)

    def test_unicity_is_by_user(self):
        self.create_record()
        self.create_record(user_id='alice')  # not raising

    def test_unicity_is_for_non_null_values(self):
        self.create_record({'phone': None})
        self.create_record({'phone': None})  # not raising

    def test_unicity_does_not_apply_to_deleted_records(self):
        record = self.create_record()
        self.backend.delete(self.resource, self.user_id, record['id'])
        self.create_record()  # not raising

    def test_unicity_applies_to_one_of_all_fields_specified(self):
        self.resource.mapping.Options.unique_fields = ('phone', 'line')
        self.create_record({'phone': 'abc', 'line': '1'})
        self.assertRaises(exceptions.UnicityError,
                          self.create_record,
                          {'phone': 'efg', 'line': '1'})

    def test_updating_with_same_id_does_not_raise_unicity_error(self):
        record = self.create_record()
        self.backend.update(self.resource, self.user_id, record['id'], record)

    def test_updating_raises_unicity_error(self):
        self.create_record({'phone': 'number'})
        record = self.create_record()
        self.assertRaises(exceptions.UnicityError,
                          self.backend.update,
                          self.resource,
                          self.user_id,
                          record['id'],
                          {'phone': 'number'})


class BackendTest(ThreadMixin,
                  FieldsUnicityTest,
                  TimestampsTest,
                  BaseTestBackend):
    """Compound of all backend tests."""
    pass


class RedisBackendTest(BackendTest, unittest.TestCase):
    def setUp(self):
        self.backend = Redis()
        super(RedisBackendTest, self).setUp()

    def test_get_all_handle_expired_values(self):
        record = '{"id": "foo"}'.encode('utf-8')
        mocked_smember = mock.patch.object(self.backend._client, "smembers",
                                           return_value=['a', 'b'])
        mocked_mget = mock.patch.object(self.backend._client, "mget",
                                        return_value=[record, None])
        with mocked_smember:
            with mocked_mget:
                self.backend.get_all("foobar", "alexis")  # not raising

    def test_ping_returns_an_error_if_unavailable(self):
        self.backend._client.setex = mock.MagicMock(
            side_effect=redis.RedisError)
        self.assertFalse(self.backend.ping())

    def test_load_redis_from_config(self):
        class config:
            class registry:
                settings = {}

        load_redis_from_config(config)


class MemoryBackendTest(BackendTest, unittest.TestCase):
    def setUp(self):
        self.backend = Memory()
        super(MemoryBackendTest, self).setUp()

    def test_ping_returns_an_error_if_unavailable(self):
        pass