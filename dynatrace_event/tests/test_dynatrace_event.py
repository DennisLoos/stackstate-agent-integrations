# (C) StackState 2021
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)
import json
import os

import mock
import requests_mock

from stackstate_checks.base import AgentCheck, StateDescriptor
from stackstate_checks.base.stubs import aggregator, telemetry
from stackstate_checks.dynatrace_event.dynatrace_event import State

CHECK_NAME = 'dynatrace_event'


def test_check_for_empty_events(dynatrace_event_check, test_instance, state):
    """
    Testing Dynatrace event check should not produce any events
    """
    with requests_mock.Mocker() as m:
        url = '{}/api/v1/events?from={}'.format(test_instance['url'],
                                                dynatrace_event_check.generate_bootstrap_timestamp(5))
        m.get(url, status_code=200, text=read_file('no_events.json'))
        dynatrace_event_check.run()
        aggregator.assert_service_check(CHECK_NAME, count=1, status=AgentCheck.OK)
        assert len(aggregator.events) == 0


def test_check_for_event_limit_reached_condition(dynatrace_event_check, test_instance):
    """
    Testing Dynatrace should throw `EventLimitReachedException` if the number of events
    between subsequent check runs exceed the `events_process_limit`
    """
    with requests_mock.Mocker() as m:
        url = '{}/api/v1/events?from={}'.format(test_instance['url'],
                                                dynatrace_event_check.generate_bootstrap_timestamp(5))
        m.get(url, status_code=200, text=read_file('21_events.json'))
        dynatrace_event_check.run()
        aggregator.assert_service_check(CHECK_NAME, count=1, status=AgentCheck.WARNING,
                                        message='Maximum event limit to process is 10 but received total 21 events')


def test_check_respects_events_process_limit_on_startup(dynatrace_event_check, test_instance):
    """
    Testing Dynatrace should respect `events_process_limit` config and just produce those number of events
    """
    with requests_mock.Mocker() as m:
        url1 = '{}/api/v1/events?from={}'.format(test_instance['url'],
                                                 dynatrace_event_check.generate_bootstrap_timestamp(5))
        url2 = '{}/api/v1/events?cursor={}'.format(test_instance['url'], '123')
        url3 = '{}/api/v1/events?cursor={}'.format(test_instance['url'], '345')
        m.get(url1, status_code=200, text=read_file("events_set1.json"))
        m.get(url2, status_code=200, text=read_file("events_set2.json"))
        m.get(url3, status_code=200, text=read_file("events_set3.json"))
        dynatrace_event_check.run()
        aggregator.assert_service_check(CHECK_NAME, count=1, status=AgentCheck.OK)
        assert len(aggregator.events) == 12


def test_check_for_error_in_events(dynatrace_event_check):
    """
    Testing Dynatrace check should produce service check when error thrown
    """
    error_msg = "mocked test error occurred"
    dynatrace_event_check._get_dynatrace_event_json_response = mock.MagicMock(
        return_value={"error": {"message": error_msg}})
    dynatrace_event_check.run()
    aggregator.assert_service_check(CHECK_NAME, count=1, status=AgentCheck.CRITICAL,
                                    message='Error in pulling the events: {}'.format(error_msg))
    assert len(aggregator.events) == 0


def test_check_raise_exception_for_response_code_not_200(dynatrace_event_check, test_instance):
    """
    Test to raise a check exception when API endpoint when status code is not 200
    """

    with requests_mock.Mocker() as m:
        url = '{}/api/v1/events?from={}'.format(test_instance['url'],
                                                dynatrace_event_check.generate_bootstrap_timestamp(5))
        m.get(url, status_code=500, text='error')
        dynatrace_event_check.run()
        error_message = 'Got 500 when hitting https://instance.live.dynatrace.com/api/v1/events'
        aggregator.assert_service_check(CHECK_NAME, count=1, status=AgentCheck.CRITICAL, message=error_message)
        assert len(aggregator.events) == 0


def test_check_raise_exception(dynatrace_event_check):
    """
    Test to raise a exception from code that talks to API endpoint throws exception
    """
    dynatrace_event_check._get_dynatrace_event_json_response = mock.MagicMock(
        side_effect=Exception("Mocked exception occurred"))
    dynatrace_event_check.run()
    assert len(aggregator.events) == 0
    aggregator.assert_service_check(CHECK_NAME, count=1, status=AgentCheck.CRITICAL,
                                    message='Mocked exception occurred')


def test_check_for_generated_events(dynatrace_event_check, test_instance):
    """
    Testing Dynatrace check should produce full events
    """
    empty_state_timestamp = dynatrace_event_check.generate_bootstrap_timestamp(5)
    with requests_mock.Mocker() as m:
        url = '{}/api/v1/events?from={}'.format(test_instance['url'], empty_state_timestamp)
        m.get(url, status_code=200, text=read_file('9_events.json'))
        dynatrace_event_check._current_time_seconds = mock.MagicMock(return_value=1613485584)
        dynatrace_event_check.run()
        aggregator.assert_service_check(CHECK_NAME, count=1, status=AgentCheck.OK)
        assert len(aggregator.events) == 8
        assert len(telemetry._topology_events) == 1
        processed_events = read_json_from_file('processed_events.json')
        for event in processed_events:
            aggregator.assert_event(event.get('msg_text'))
        processed_topology_events = read_json_from_file('processed_topology_events.json')
        for event in processed_topology_events:
            telemetry.assert_topology_event(event)


def test_state_data(state, dynatrace_event_check, test_instance):
    """
    Check is the right timestamp is writen to the state
    """
    state_instance = StateDescriptor("instance.dynatrace_event.https_instance.live.dynatrace.com", "dynatrace_event.d")
    state.assert_state(state_instance, None)
    events_file = 'no_events.json'
    with requests_mock.Mocker() as m:
        url = '{}/api/v1/events?from={}'.format(test_instance['url'],
                                                dynatrace_event_check.generate_bootstrap_timestamp(5))
        m.get(url, status_code=200, text=read_file(events_file))
        dynatrace_event_check.run()
        aggregator.assert_service_check(CHECK_NAME, count=1, status=AgentCheck.OK)
    mocked_response_data = read_json_from_file(events_file)
    new_state = State({'last_processed_event_timestamp': mocked_response_data.get('to')})
    state.assert_state(state_instance, new_state)


def read_file(filename):
    with open(get_path_to_file(filename), "r") as f:
        return f.read()


def read_json_from_file(filename):
    with open(get_path_to_file(filename), 'r') as f:
        return json.load(f)


def get_path_to_file(filename):
    path_to_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'samples', filename)
    return path_to_file