"""
Copyright 2017-present, Airbnb Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
# pylint: disable=no-self-use,protected-access,attribute-defined-outside-init
import json
import os

from mock import patch
from nose.tools import (
    assert_equal,
    assert_false,
    assert_in,
    assert_items_equal,
    assert_true,
)

from stream_alert.rule_processor.config import load_config
from stream_alert.rule_processor.parsers import get_parser
from stream_alert.rule_processor.rules_engine import RulesEngine
from stream_alert.shared import NORMALIZATION_KEY
from stream_alert.shared.rule import disable, matcher, Matcher, rule, Rule

from tests.unit.stream_alert_rule_processor.test_helpers import (
    load_and_classify_payload,
    make_kinesis_raw_record,
    MockDynamoDBClient,
    mock_normalized_records,
)
from helpers.base import fetch_values_by_datatype


@patch.dict(os.environ, {'CLUSTER': ''})
class TestRulesEngine(object):
    """Test class for RulesEngine"""

    def setup(self):
        """Setup before each method"""
        # Clear out the cached matchers and rules to avoid conflicts with production code
        Matcher._matchers.clear()
        Rule._rules.clear()
        self.config = load_config('tests/unit/conf')
        self.config['global']['threat_intel']['enabled'] = False
        self.rules_engine = RulesEngine(self.config)

    def test_basic_rule_matcher_process(self):
        """Rules Engine - Basic Rule/Matcher"""
        @matcher
        def prod(rec):  # pylint: disable=unused-variable
            return rec['environment'] == 'prod'

        @rule(logs=['test_log_type_json_nested_with_data'],
              outputs=['s3:sample_bucket'])
        def minimal_rule(rec):  # pylint: disable=unused-variable
            return rec['unixtime'] == 1483139547

        @rule(matchers=['prod'],
              logs=['test_log_type_json_nested_with_data'],
              outputs=['pagerduty:sample_integration'])
        def chef_logs(rec):  # pylint: disable=unused-variable
            return rec['application'] == 'chef'

        @rule(matchers=['prod'],
              logs=['test_log_type_json_nested_with_data'],
              outputs=['pagerduty:sample_integration'])
        def test_nest(rec):  # pylint: disable=unused-variable
            return rec['data']['source'] == 'eu'

        kinesis_data = {
            'date': 'Dec 01 2016',
            'unixtime': '1483139547',
            'host': 'host1.web.prod.net',
            'application': 'chef',
            'environment': 'prod',
            'data': {
                'category': 'web-server',
                'type': '1',
                'source': 'eu'
            }
        }

        # prepare the payloads
        service, entity = 'kinesis', 'test_kinesis_stream'
        raw_record = make_kinesis_raw_record(entity, json.dumps(kinesis_data))
        payload = load_and_classify_payload(self.config, service, entity, raw_record)

        # process payloads
        alerts, _ = self.rules_engine.run(payload)

        # check alert output
        assert_equal(len(alerts), 3)
        rule_outputs_map = {
            'chef_logs': ['pagerduty:sample_integration'],
            'minimal_rule': ['s3:sample_bucket'],
            'test_nest': ['pagerduty:sample_integration']
        }
        # doing this because after kinesis_data is read in, types are casted per
        # the schema
        for alert in alerts:
            if NORMALIZATION_KEY in alert.record.keys():
                alert.record.remove(NORMALIZATION_KEY)
            assert_items_equal(alert.record.keys(), kinesis_data.keys())
            assert_in(rule_outputs_map[alert.rule_name][0], alert.outputs)

    def test_process_subkeys_nested_records(self):
        """Rules Engine - Required Subkeys with Nested Records"""
        def cloudtrail_us_east_logs(rec):
            return (
                'us-east' in rec['awsRegion'] and
                'AWS' in rec['requestParameters']['program']
            )
        rule_attrs = Rule(
            cloudtrail_us_east_logs,
            rule_name='cloudtrail_us_east_logs',
            matchers=[],
            datatypes=[],
            logs=['test_log_type_json_nested'],
            merge_by_keys=[],
            merge_window_mins=0,
            outputs=['s3:sample_bucket'],
            req_subkeys={'requestParameters': ['program']},
            context={}
        )

        data = json.dumps({
            'Records': [
                {
                    'eventVersion': '1.05',
                    'eventID': '2',
                    'eventTime': '3',
                    'requestParameters': {
                        'program': 'AWS CLI'
                    },
                    'eventType': 'CreateSomeResource',
                    'responseElements': 'Response',
                    'awsRegion': 'us-east-1',
                    'eventName': 'CreateResource',
                    'userIdentity': {
                        'name': 'john',
                        'key': 'AVC124313414'
                    },
                    'eventSource': 'Kinesis',
                    'requestID': '12345',
                    'userAgent': 'AWS CLI v1.3109',
                    'sourceIPAddress': '127.0.0.1',
                    'recipientAccountId': '123456123456'
                },
                {
                    'eventVersion': '1.05',
                    'eventID': '2',
                    'eventTime': '3',
                    'requestParameters': {
                        'program': 'AWS UI'
                    },
                    'eventType': 'CreateSomeOtherResource',
                    'responseElements': 'Response',
                    'awsRegion': 'us-east-2',
                    'eventName': 'CreateResource',
                    'userIdentity': {
                        'name': 'ann',
                        'key': 'AD114313414'
                    },
                    'eventSource': 'Lambda',
                    'requestID': '12345',
                    'userAgent': 'Google Chrome 42',
                    'sourceIPAddress': '127.0.0.2',
                    'recipientAccountId': '123456123456'
                },
                {
                    'eventVersion': '1.05',
                    'eventID': '2',
                    'eventTime': '3',
                    # Translates from null in JSON to None in Python
                    'requestParameters': None,
                    'eventType': 'CreateSomeResource',
                    'responseElements': 'Response',
                    'awsRegion': 'us-east-1',
                    'eventName': 'CreateResource',
                    'userIdentity': {
                        'name': 'john',
                        'key': 'AVC124313414'
                    },
                    'eventSource': 'Kinesis',
                    'requestID': '12345',
                    'userAgent': 'AWS CLI',
                    'sourceIPAddress': '127.0.0.1',
                    'recipientAccountId': '123456123456'
                }
            ]
        })

        schema = self.config['logs']['test_cloudtrail']['schema']
        options = self.config['logs']['test_cloudtrail']['configuration']

        parser_class = get_parser('json')
        parser = parser_class(options)
        parsed_result = parser.parse(schema, data)

        valid_record = [
            rec for rec in parsed_result if rec['requestParameters'] is not None][0]
        valid_subkey_check = RulesEngine.process_subkeys(valid_record, 'json', rule_attrs)
        assert_true(valid_subkey_check)

        invalid_record = [
            rec for rec in parsed_result if rec['requestParameters'] is None][0]
        invalid_subkey_check = RulesEngine.process_subkeys(invalid_record, 'json', rule_attrs)
        assert_false(invalid_subkey_check)

    def test_process_subkeys(self):
        """Rules Engine - Req Subkeys"""
        @rule(logs=['test_log_type_json_nested'],
              outputs=['s3:sample_bucket'],
              req_subkeys={'data': ['location']})
        def data_location(rec):  # pylint: disable=unused-variable
            return rec['data']['location'].startswith('us')

        @rule(logs=['test_log_type_json_nested'],
              outputs=['s3:sample_bucket'],
              req_subkeys={'data': ['category']})
        def web_server(rec):  # pylint: disable=unused-variable
            return rec['data']['category'] == 'web-server'

        kinesis_data_items = [
            {
                'date': 'Dec 01 2016',
                'unixtime': '1483139547',
                'host': 'host1.web.prod.net',
                'data': {
                    'category': 'web-server',
                    'type': '1',
                    'source': 'eu'
                }
            },
            {
                'date': 'Dec 01 2016',
                'unixtime': '1483139547',
                'host': 'host1.web.prod.net',
                'data': {
                    'location': 'us-west-2'
                }
            }
        ]

        # prepare payloads
        alerts = []
        for data in kinesis_data_items:
            kinesis_data = json.dumps(data)
            # prepare the payloads
            service, entity = 'kinesis', 'test_kinesis_stream'
            raw_record = make_kinesis_raw_record(entity, kinesis_data)
            payload = load_and_classify_payload(self.config, service, entity, raw_record)

            alerts.extend(self.rules_engine.run(payload)[0])

        # check alert output
        assert_equal(len(alerts), 2)

        # alert tests
        assert_equal(alerts[0].rule_name, 'web_server')
        assert_equal(alerts[1].rule_name, 'data_location')

    def test_syslog_rule(self):
        """Rules Engine - Syslog Rule"""
        @rule(logs=['test_log_type_syslog'],
              outputs=['s3:sample_bucket'])
        def syslog_sudo(rec):  # pylint: disable=unused-variable
            return (
                rec['application'] == 'sudo' and
                'root' in rec['message']
            )

        kinesis_data = (
            'Jan 26 19:35:33 vagrant-ubuntu-trusty-64 '
            'sudo: pam_unix(sudo:session): '
            'session opened for user root by (uid=0)'
        )
        # prepare the payloads
        service, entity = 'kinesis', 'test_stream_2'
        raw_record = make_kinesis_raw_record(entity, kinesis_data)
        payload = load_and_classify_payload(self.config, service, entity, raw_record)

        # process payloads
        alerts, _ = self.rules_engine.run(payload)

        # alert tests
        assert_equal(len(alerts), 1)
        assert_equal(alerts[0].rule_name, 'syslog_sudo')
        assert_equal(alerts[0].record['host'], 'vagrant-ubuntu-trusty-64')
        assert_equal(alerts[0].log_type, 'syslog')

    def test_csv_rule(self):
        """Rules Engine - CSV Rule"""
        @rule(logs=['test_log_type_csv_nested'],
              outputs=['pagerduty:sample_integration'])
        def nested_csv(rec):  # pylint: disable=unused-variable
            return (
                rec['message']['application'] == 'chef' and
                rec['message']['cluster_size'] == 100
            )

        kinesis_data = (
            '"Jan 10, 2017","1485739910","host1.prod.test","Corp",'
            '"chef,web-server,1,100,fail"'
        )
        # prepare the payloads
        service, entity = 'kinesis', 'test_kinesis_stream'
        raw_record = make_kinesis_raw_record(entity, kinesis_data)
        payload = load_and_classify_payload(self.config, service, entity, raw_record)

        # process payloads
        alerts, _ = self.rules_engine.run(payload)

        # alert tests
        assert_equal(len(alerts), 1)
        assert_equal(alerts[0].rule_name, 'nested_csv')

    def test_rule_disable(self):
        """Rules Engine - Disable Rule"""
        @disable
        @rule(logs=['test_log_type_json_2'],
              outputs=['pagerduty:sample_integration'])
        def nested_csv_disable_test(rec):  # pylint: disable=unused-variable
            return rec['host'] == 'unit-test-host.prod.test'

        kinesis_data = json.dumps({
            'key4': True,
            'key5': 0.0,
            'key6': 1,
            'key7': False
        })

        # prepare the payloads
        service, entity = 'kinesis', 'test_kinesis_stream'
        raw_record = make_kinesis_raw_record(entity, kinesis_data)
        payload = load_and_classify_payload(self.config, service, entity, raw_record)

        # process payloads
        alerts, _ = self.rules_engine.run(payload)

        # alert tests
        assert_equal(len(alerts), 0)

    def test_kv_rule(self):
        """Rules Engine - KV Rule"""
        @rule(logs=['test_log_type_kv_auditd'],
              outputs=['pagerduty:sample_integration'])
        def auditd_bin_cat(rec):  # pylint: disable=unused-variable
            return (
                rec['type'] == 'SYSCALL' and
                rec['exe'] == '"/bin/cat"'
            )

        @rule(logs=['test_log_type_kv_auditd'],
              outputs=['pagerduty:sample_integration'])
        def gid_500(rec):  # pylint: disable=unused-variable
            return (
                rec['gid'] == 500 and
                rec['euid'] == 500
            )

        auditd_test_data = (
            'type=SYSCALL msg=audit(1364481363.243:24287): '
            'arch=c000003e syscall=2 success=no exit=-13 a0=7fffd19c5592 a1=0 '
            'a2=7fffd19c4b50 a3=a items=1 ppid=2686 pid=3538 auid=500 uid=500 '
            'gid=500 euid=500 suid=500 fsuid=500 egid=500 sgid=500 fsgid=500 tty=pts0 '
            'ses=1 comm="cat" exe="/bin/cat" '
            'subj=unconfined_u:unconfined_r:unconfined_t:s0-s0:c0.c1023 '
            'key="sshd_config" type=CWD msg=audit(1364481363.243:24287):  '
            'cwd="/home/shadowman" type=PATH '
            'msg=audit(1364481363.243:24287): item=0 name="/etc/ssh/sshd_config" '
            'inode=409248 dev=fd:00 mode=0100600 ouid=0 ogid=0 '
            'rdev=00:00 obj=system_u:object_r:etc_t:s0'
        )

        # prepare the payloads
        service, entity = 'kinesis', 'test_kinesis_stream'
        raw_record = make_kinesis_raw_record(entity, auditd_test_data)
        payload = load_and_classify_payload(self.config, service, entity, raw_record)

        # process payloads
        alerts, _ = self.rules_engine.run(payload)

        # alert tests
        assert_equal(len(alerts), 2)

        rule_name_alerts = [x.rule_name for x in alerts]
        assert_items_equal(rule_name_alerts, ['gid_500', 'auditd_bin_cat'])

    def test_match_types(self):
        """Rules Engine - Match normalized types against record"""
        @rule(logs=['cloudwatch:test_match_types'],
              outputs=['s3:sample_bucket'],
              datatypes=['sourceAddress'])
        def match_ipaddress(rec): # pylint: disable=unused-variable
            """Testing rule to detect matching IP address

            Datatype 'sourceAddress' is defined in tests/unit/conf/types.json
            for cloudwatch logs. This rule should be trigger by testing event.
            """
            results = fetch_values_by_datatype(rec, 'sourceAddress')

            return any(result == '1.1.1.2' for result in results)

        @rule(logs=['cloudwatch:test_match_types'],
              outputs=['s3:sample_bucket'],
              datatypes=['sourceAddress', 'command'])
        def mismatch_types(rec): # pylint: disable=unused-variable
            """Testing rule with non-existing normalized type in the record.

            Datatype 'sourceAddress' is defined in tests/unit/conf/types.json
            for cloudwatch logs, but 'command' is not. This rule should be
            triggered by testing event since we change rule parameter 'datatypes'
            to OR operation among CEF types. See the discussion at
            https://github.com/airbnb/streamalert/issues/365
            """
            results = fetch_values_by_datatype(rec, 'sourceAddress')

            return any(result == '2.2.2.2' for result in results)

        kinesis_data_items = [
            {
                'account': 123456,
                'region': '123456123456',
                'source': '1.1.1.2',
                'detail': {
                    'eventName': 'ConsoleLogin',
                    'sourceIPAddress': '1.1.1.2',
                    'recipientAccountId': '654321'
                }
            },
            {
                'account': 654321,
                'region': '654321654321',
                'source': '2.2.2.2',
                'detail': {
                    'eventName': 'ConsoleLogin',
                    'sourceIPAddress': '2.2.2.2',
                    'recipientAccountId': '123456'
                }
            }
        ]

        # prepare payloads
        alerts = []
        for data in kinesis_data_items:
            kinesis_data = json.dumps(data)
            # prepare the payloads
            service, entity = 'kinesis', 'test_kinesis_stream'
            raw_record = make_kinesis_raw_record(entity, kinesis_data)
            payload = load_and_classify_payload(self.config, service, entity, raw_record)

            alerts.extend(self.rules_engine.run(payload)[0])

        # check alert output
        assert_equal(len(alerts), 2)

        # alert tests
        assert_equal(alerts[0].rule_name, 'match_ipaddress')

    def test_update(self):
        """Rules Engine - Update results passed to update method"""
        results = {
            'ipv4': [['key1']]
        }
        parent_key = 'key2'
        nested_results = {
            'username': [['sub_key1']],
            'ipv4': [['sub_key2']]
        }
        RulesEngine.update(results, parent_key, nested_results)
        expected_results = {
            'username': [['key2', 'sub_key1']],
            'ipv4': [['key1'], ['key2', 'sub_key2']]
        }
        assert_equal(results.keys(), expected_results.keys())
        assert_equal(results['ipv4'], expected_results['ipv4'])
        assert_equal(results['username'], expected_results['username'])

        results = {
            'ipv4': [['key1'], ['key3', 'sub_key3', 'sub_key4']],
            'type': [['key4']]
        }
        parent_key = 'key2'
        nested_results = {
            'username': [['sub_key1', 'sub_key11']],
            'type': [['sub_key2']]
        }
        RulesEngine.update(results, parent_key, nested_results)
        expected_results = {
            'username': [['key2', 'sub_key1', 'sub_key11']],
            'type': [['key4'], ['key2', 'sub_key2']],
            'ipv4': [['key1'], ['key3', 'sub_key3', 'sub_key4']]
        }
        assert_equal(results.keys(), expected_results.keys())
        assert_equal(results['ipv4'], expected_results['ipv4'])
        assert_equal(results['username'], expected_results['username'])
        assert_equal(results['type'], expected_results['type'])

    def test_match_types_helper(self):
        """Rules Engine - Recursively walk though all nested keys and update
        return results.
        """
        record = {
            'account': 123456,
            'region': 'region_name',
            'detail': {
                'eventType': 'Decrypt',
                'awsRegion': 'region_name',
                'source': '1.1.1.2'
            },
            'sourceIPAddress': '1.1.1.2'
        }
        normalized_types = {
            'account': ['account'],
            'region': ['region', 'awsRegion'],
            'ipv4': ['destination', 'source', 'sourceIPAddress']
        }
        datatypes = ['account', 'ipv4', 'region']
        results = RulesEngine.match_types_helper(
            record,
            normalized_types,
            datatypes
            )
        expected_results = {
            'account': [['account']],
            'ipv4': [['sourceIPAddress'], ['detail', 'source']],
            'region': [['region'], ['detail', 'awsRegion']]
        }
        assert_equal(results, expected_results)

        # When multiple subkeys presented with same normalized type
        record = {
            'account': 123456,
            'region': 'region_name',
            'detail': {
                'eventType': 'Decrypt',
                'awsRegion': 'region_name',
                'source': '1.1.1.2',
                'userIdentity': {
                    "userName": "Alice",
                    "principalId": "...",
                    "invokedBy": "signin.amazonaws.com"
                }
            },
            'sourceIPAddress': '1.1.1.2'
        }
        normalized_types = {
            'account': ['account'],
            'region': ['region', 'awsRegion'],
            'ipv4': ['destination', 'source', 'sourceIPAddress'],
            'userName': ['userName', 'owner', 'invokedBy']
        }
        datatypes = ['account', 'ipv4', 'region', 'userName']
        results = RulesEngine.match_types_helper(
            record,
            normalized_types,
            datatypes
            )
        expected_results = {
            'account': [['account']],
            'ipv4': [['sourceIPAddress'], ['detail', 'source']],
            'region': [['region'], ['detail', 'awsRegion']],
            'userName': [
                ['detail', 'userIdentity', 'userName'],
                ['detail', 'userIdentity', 'invokedBy']
            ]
        }
        assert_equal(results, expected_results)

    def test_process_optional_logs(self):
        """Rules Engine - Logs is optional when datatypes are present"""
        @rule(datatypes=['sourceAddress'],
              outputs=['s3:sample_bucket'])
        def no_logs_has_datatypes(rec): # pylint: disable=unused-variable
            """Testing rule when logs is not present, datatypes is"""
            results = fetch_values_by_datatype(rec, 'sourceAddress')

            for result in results:
                if result == '1.1.1.2':
                    return True
            return False

        @rule(logs=['cloudwatch:test_match_types'],
              outputs=['s3:sample_bucket'])
        def has_logs_no_datatypes(rec): # pylint: disable=unused-variable
            """Testing rule when logs is present, datatypes is not"""

            return (
                rec['source'] == '1.1.1.2' or
                rec['detail']['sourceIPAddress'] == '1.1.1.2'
            )

        @rule(logs=['cloudwatch:test_match_types'],
              datatypes=['sourceAddress'],
              outputs=['s3:sample_bucket'])
        def has_logs_datatypes(rec): # pylint: disable=unused-variable
            """Testing rule when logs is present, datatypes is"""
            results = fetch_values_by_datatype(rec, 'sourceAddress')

            for result in results:
                if result == '1.1.1.2':
                    return True
            return False

        kinesis_data_items = [
            {
                'account': 123456,
                'region': '123456123456',
                'source': '1.1.1.2',
                'detail': {
                    'eventName': 'ConsoleLogin',
                    'sourceIPAddress': '1.1.1.2',
                    'recipientAccountId': '654321'
                }
            }
        ]

        alerts = []
        for data in kinesis_data_items:
            kinesis_data = json.dumps(data)
            service, entity = 'kinesis', 'test_kinesis_stream'
            raw_record = make_kinesis_raw_record(entity, kinesis_data)
            payload = load_and_classify_payload(self.config, service, entity, raw_record)

            alerts.extend(self.rules_engine.run(payload)[0])

        assert_equal(len(alerts), 3)
        rule_names = ['no_logs_has_datatypes',
                      'has_logs_no_datatypes',
                      'has_logs_datatypes']
        assert_items_equal([alerts[i].rule_name for i in range(3)], rule_names)

    def test_reset_normalized_types(self):
        """Rules Engine - Normalized types should be reset after each iteration"""
        @rule(datatypes=['sourceAddress'],
              outputs=['s3:sample_bucket'])
        def test_01_matching_sourceaddress_datatypes(rec): # pylint: disable=unused-variable
            """Testing rule to alert on matching sourceAddress"""
            results = fetch_values_by_datatype(rec, 'sourceAddress')

            for result in results:
                if result == '1.1.1.2':
                    return True
            return False

        @rule(logs=['cloudwatch:test_match_types', 'test_log_type_json_nested'],
              outputs=['s3:sample_bucket'])
        def test_02_rule_without_datatypes(_): # pylint: disable=unused-variable
            """Testing rule without datatypes parameter"""
            return True

        kinesis_data_items = [
            {
                'account': 123456,
                'region': '123456123456',
                'source': '1.1.1.2',
                'detail': {
                    'eventName': 'ConsoleLogin',
                    'sourceIPAddress': '1.1.1.2',
                    'recipientAccountId': '654321'
                }
            },
            {
                'date': 'Dec 01 2016',
                'unixtime': '1483139547',
                'host': 'host1.web.prod.net',
                'data': {
                    'category': 'web-server',
                    'type': '1',
                    'source': 'eu'
                }
            }
        ]

        alerts = []
        for data in kinesis_data_items:
            kinesis_data = json.dumps(data)
            service, entity = 'kinesis', 'test_kinesis_stream'
            raw_record = make_kinesis_raw_record(entity, kinesis_data)
            payload = load_and_classify_payload(self.config, service, entity, raw_record)

            alerts.extend(self.rules_engine.run(payload)[0])

        assert_equal(len(alerts), 3)
        for alert in alerts:
            has_key_normalized_types = NORMALIZATION_KEY in alert.record
            if alert.rule_name == 'test_02_rule_without_datatypes':
                assert_equal(has_key_normalized_types, False)
            else:
                assert_equal(has_key_normalized_types, True)

    @patch('boto3.client')
    def test_process_with_threat_intel_enabled(self, mock_client):
        """Rules Engine - Threat Intel is enabled when process method is called"""
        @rule(datatypes=['sourceAddress'], outputs=['s3:sample_bucket'])
        def match_ipaddress(_): # pylint: disable=unused-variable
            """Testing dummy rule"""
            return True

        mock_client.return_value = MockDynamoDBClient()
        toggled_config = self.config
        toggled_config['global']['threat_intel']['enabled'] = True
        toggled_config['global']['threat_intel']['dynamodb_table'] = 'test_table_name'

        new_rules_engine = RulesEngine(toggled_config)
        kinesis_data_items = [
            {
                'account': 123456,
                'region': '123456123456',
                'source': '1.1.1.2',
                'detail': {
                    'eventName': 'ConsoleLogin',
                    'sourceIPAddress': '1.1.1.2',
                    'recipientAccountId': '654321'
                }
            }
        ]

        for data in kinesis_data_items:
            kinesis_data = json.dumps(data)
            service, entity = 'kinesis', 'test_kinesis_stream'
            raw_record = make_kinesis_raw_record(entity, kinesis_data)
            payload = load_and_classify_payload(toggled_config, service, entity, raw_record)

            assert_equal(len(new_rules_engine.run(payload)[0]), 1)

    @patch('boto3.client')
    def test_threat_intel_match(self, mock_client):
        """Rules Engine - Threat Intel is enabled when threat_intel_match is called"""
        @rule(datatypes=['sourceAddress', 'destinationDomain', 'fileHash'],
              outputs=['s3:sample_bucket'])
        def match_rule(_): # pylint: disable=unused-variable
            """Testing dummy rule"""
            return True

        mock_client.return_value = MockDynamoDBClient()
        toggled_config = self.config
        toggled_config['global']['threat_intel']['enabled'] = True
        toggled_config['global']['threat_intel']['dynamodb_table'] = 'test_table_name'

        new_rules_engine = RulesEngine(toggled_config)
        records = mock_normalized_records()
        alerts = new_rules_engine.threat_intel_match(records)
        assert_equal(len(alerts), 2)

    @patch('boto3.client')
    def test_process_allow_multi_around_normalization(self, mock_client):
        """Rules Engine - Threat Intel is enabled run multi-round_normalization"""

        @rule(datatypes=['fileHash'], outputs=['s3:sample_bucket'])
        def match_file_hash(rec): # pylint: disable=unused-variable
            """Testing dummy rule to match file hash"""
            return 'streamalert:ioc' in rec and 'md5' in rec['streamalert:ioc']

        @rule(datatypes=['fileHash'], outputs=['s3:sample_bucket'])
        def match_file_hash_again(_): # pylint: disable=unused-variable
            """Testing dummy rule to match file hash again"""
            return False

        @rule(datatypes=['fileHash', 'sourceDomain'], outputs=['s3:sample_bucket'])
        def match_source_domain(rec): # pylint: disable=unused-variable
            """Testing dummy rule to match source domain and file hash"""
            return 'streamalert:ioc' in rec

        mock_client.return_value = MockDynamoDBClient()
        toggled_config = self.config
        toggled_config['global']['threat_intel']['enabled'] = True
        toggled_config['global']['threat_intel']['dynamodb_table'] = 'test_table_name'

        new_rules_engine = RulesEngine(toggled_config)
        kinesis_data = {
            "Field1": {
                "SubField1": {
                    "key1": 17,
                    "key2_md5": "md5-of-file",
                    "key3_source_domain": "evil.com"
                },
                "SubField2": 1
            },
            "Field2": {
                "Authentication": {}
            },
            "Field3": {},
            "Field4": {}
        }

        kinesis_data = json.dumps(kinesis_data)
        service, entity = 'kinesis', 'test_stream_threat_intel'
        raw_record = make_kinesis_raw_record(entity, kinesis_data)
        payload = load_and_classify_payload(toggled_config, service, entity, raw_record)
        alerts, normalized_records = new_rules_engine.run(payload)

        # Two testing rules are for threat intelligence matching. So no alert will be
        # generated before threat intel takes effect.
        assert_equal(len(alerts), 0)

        # One record will be normalized twice by two different rules with different
        # normalization keys. It should generate two alerts by two different rules
        # from same record.
        assert_equal(len(normalized_records), 2)
        assert_equal(normalized_records[0].pre_parsed_record['streamalert:normalization'].keys(),
                     ['fileHash'])
        assert_equal(normalized_records[1].pre_parsed_record['streamalert:normalization'].keys(),
                     ['fileHash', 'sourceDomain'])

        # Pass normalized records to threat intel engine.
        alerts_from_threat_intel = new_rules_engine.threat_intel_match(normalized_records)
        assert_equal(len(alerts_from_threat_intel), 2)
        assert_equal(alerts_from_threat_intel[0].rule_name, 'match_file_hash')
        assert_equal(alerts_from_threat_intel[1].rule_name, 'match_source_domain')

    def test_rule_modify_context(self):
        """Rules Engine - Testing Context Modification"""
        @rule(logs=['test_log_type_json_nested_with_data'],
              outputs=['s3:sample_bucket'],
              context={'assigned_user': 'not_set', 'assigned_policy_id': 'not_set2'})
        def modify_context_test(rec, context): # pylint: disable=unused-variable
            """Modify context rule"""
            context['assigned_user'] = 'valid_user'
            context['assigned_policy_id'] = 'valid_policy_id'
            return rec['application'] == 'web-app'

        kinesis_data = json.dumps({
            'date': 'Dec 01 2016',
            'unixtime': '1483139547',
            'host': 'host1.web.prod.net',
            'application': 'web-app',
            'environment': 'prod',
            'data': {
                'category': 'web-server',
                'type': '1',
                'source': 'eu'
            }
        })

        # prepare the payloads
        service, entity = 'kinesis', 'test_kinesis_stream'
        raw_record = make_kinesis_raw_record(entity, kinesis_data)
        payload = load_and_classify_payload(self.config, service, entity, raw_record)

        # process payloads
        alerts, _ = self.rules_engine.run(payload)

        # alert tests
        assert_equal(alerts[0].context['assigned_user'], 'valid_user')
        assert_equal(alerts[0].context['assigned_policy_id'], 'valid_policy_id')
