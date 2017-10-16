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
from datetime import datetime
import re

from app_integrations import LOGGER
from app_integrations.apps.app_base import app, AppIntegration


@app
class OneLoginApp(AppIntegration):
    """OneLogin StreamAlert App"""
    _ONELOGIN_EVENTS_URL = 'https://api.{}.onelogin.com/api/1/events'
    _ONELOGIN_TOKEN_URL = 'https://api.{}.onelogin.com/auth/oauth2/v2/token'
    _ONELOGIN_RATE_LIMIT_URL = 'https://api.{}.onelogin.com/auth/rate_limit'
    # OneLogin API returns 50 events per page
    _MAX_EVENTS_LIMIT = 50

    # Define our authorization headers variable
    def __init__(self, config):
        super(OneLoginApp, self).__init__(config)
        self._auth_headers = None
        self._next_page_url = None
        self._rate_limit_sleep = 0

    @classmethod
    def _type(cls):
        return 'events'

    def _token_endpoint(self):
        """Get the endpoint URL to retrieve tokens

        Returns:
            str: Full URL to generate tokens for the OneLogin API
        """
        return self._ONELOGIN_TOKEN_URL.format(self._config['auth']['region'])

    def _events_endpoint(self):
        """Get the endpoint URL to retrieve events

        Returns:
            str: Full URL to retrieve events in the OneLogin API
        """
        return self._ONELOGIN_EVENTS_URL.format(self._config['auth']['region'])

    def _rate_limit_endpoint(self):
        """Get the endpoint URL to retrieve rate limit details

        Returns:
            str: Full URL to retrieve rate limit details in the OneLogin API
        """
        return self._ONELOGIN_RATE_LIMIT_URL.format(self._config['auth']['region'])

    @classmethod
    def service(cls):
        return 'onelogin'

    def _generate_headers(self):
        """Each request will request a new token to call the resources APIs.

        More details to be found here:
            https://developers.onelogin.com/api-docs/1/oauth20-tokens/generate-tokens-2

        Returns:
            str: Bearer token to be used to call the OneLogin resource APIs
        """
        authorization = 'client_id: {}, client_secret: {}'.format(
            self._config['auth']['client_id'], self._config['auth']['client_secret'])

        headers_token = {'Authorization': authorization,
                         'Content-Type': 'application/json'}

        result, response = self._make_post_request(self._token_endpoint(),
                                                   headers_token,
                                                   {'grant_type':'client_credentials'})

        if not result:
            return False

        if not response:
            LOGGER.error('Response invalid generating headers for service \'%s\'',
                         self.type())
            return False

        bearer = 'bearer:{}'.format(response.get('access_token'))
        self._auth_headers = {'Authorization': bearer}

    def _gather_logs(self):
        """Gather the authentication log events."""
        if not self._auth_headers:
            self._auth_headers = self._generate_headers()

        return self._get_onelogin_events()

    def _set_rate_limit_sleep(self):
        """Get the number of seconds we need to sleep until we are clear to continue"""
        # Make sure we have authentication headers
        if not self._auth_headers:
            self._rate_limit_sleep = 0
            LOGGER.error('No authentication headers for service \'%s\'',
                         self.type())
            return

        result, response = self._make_get_request(self._rate_limit_endpoint(),
                                                  self._auth_headers)

        if not result:
            self._rate_limit_sleep = 0
            return

        # Making sure we have a valid response
        if not response:
            LOGGER.error('Response invalid getting rate limit data for service \'%s\'',
                         self.type())
            self._rate_limit_sleep = 0
            return

        self._rate_limit_sleep = response.get('data')['X-RateLimit-Reset']
        LOGGER.info('Rate limit sleep set for \'%s\': %d', self.type(), self._rate_limit_sleep)

    def _get_onelogin_events(self):
        """Get all events from the endpoint for this timeframe

        Returns:
            [
                {
                    'id': <int:id>,
                    'created_at': <str:created_at>,
                    'account_id': <int:account_id>,
                    'user_id': <int:user_id>,
                    'event_type_id': <int:event_type_id>,
                    'notes': <str:notes>,
                    'ipaddr': <str:ipaddr>,
                    'actor_user_id': <int:actor_user_id>,
                    'assuming_acting_user_id': null,
                    'role_id': <int:role_id>,
                    'app_id': <int:app_id>,
                    'group_id': <int:group_id>,
                    'otp_device_id': <int:otp_device_id>,
                    'policy_id': <int:policy_id>,
                    'actor_system': <str:actor_system>,
                    'custom_message': <str:custom_message>,
                    'role_name': <str:role_name>,
                    'app_name': <str:app_name>,
                    'group_name': <str:group_name>,
                    'actor_user_name': <str:actor_user_name>,
                    'user_name': <str:user_name>,
                    'policy_name': <str:policy_name>,
                    'otp_device_name': <str:otp_device_name>,
                    'operation_name': <str:operation_name>,
                    'directory_sync_run_id': <int:directory_sync_run_id>,
                    'directory_id': <int:directory_id>,
                    'resolution': <str:resolution>,
                    'client_id': <int:client_id>,
                    'resource_type_id': <int:resource_type_id>,
                    'error_description': <str:error_description>
                }
            ]
        """
        # Make sure we have authentication headers
        if not self._auth_headers:
            LOGGER.error('No authentication headers for service \'%s\'',
                         self.type())
            return False

        # Are we just getting events or getting paginated events?
        if self._next_page_url:
            params = None
            request_url = self._next_page_url
        else:
            # Check the type to understand the format stored
            if isinstance(self._last_timestamp, int):
                # OneLogin API expects the ISO 8601 format: YYYY-MM-DDTHH:MM:SSZ
                formatted_date = datetime.utcfromtimestamp(
                    self._last_timestamp).strftime('%Y-%m-%dT%H:%M:%SZ')
            else:
                # We should have already this timestamp in format ISO 8601
                formatted_date = self._last_timestamp

            params = {'since': formatted_date}
            request_url = self._events_endpoint()

        result, response = self._make_get_request(request_url, self._auth_headers, params)

        if not result:
            # If we hit the rate limit, update the sleep time
            if response.get('status'):
                r_status = response.get('status')
                if r_status['code'] == 400 and r_status['message'] == 'rate_limit_exceeded':
                    self._set_rate_limit_sleep()

            return False

        # Fail if response is invalid
        if not response:
            LOGGER.error('Response is invalid for service \'%s\'',
                         self.type())
            return False

        # Set pagination link, if there is any
        pagination = response.get('pagination')
        self._next_page_url = pagination['next_link']
        self._more_to_poll = bool(self._next_page_url)

        # Adjust the last seen event
        events = response.get('data')
        self._last_timestamp = events[-1]['created_at']

        # Return the list of events to the caller so they can be send to the batcher
        return events

    def required_auth_info(self):
        return {
            'region':
                {
                    'description': ('the region for the OneLogin API. This should be'
                                    'just "en" or "us".'),
                    'format': re.compile(r'^(en|us)$')
                },
            'client_secret':
                {
                    'description': ('the client secret for the OneLogin API. This '
                                    'should be a string of 57 alphanumeric characters'),
                    'format': re.compile(r'^[a-zA-Z0-9]{57}$')
                },
            'client_id':
                {
                    'description': ('the client id for the OneLogin API. This '
                                    'should be a string of 57 alphanumeric characters'),
                    'format': re.compile(r'^[a-zA-Z0-9]{57}$')
                }
            }

    def _sleep_seconds(self):
        """Return the number of seconds this polling function should sleep for
        between requests to avoid failed requests. OneLogin tokens allows for 5000 requests
        every hour, but if the rate limit is reached, we can retrieve how long until we are clear.

        More information about this here:
            https://developers.onelogin.com/api-docs/1/oauth20-tokens/get-rate-limit

        Returns:
            int: Number of seconds that this function should sleep for between requests
        """
        return self._rate_limit_sleep
