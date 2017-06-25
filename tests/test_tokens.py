from __future__ import unicode_literals

from datetime import datetime, timedelta

from django.test import TestCase
from django.utils.six import text_type
from jose import jwt
from mock import patch
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.state import User
from rest_framework_simplejwt.tokens import (
    AccessToken, RefreshToken, SlidingToken, Token
)
from rest_framework_simplejwt.utils import datetime_to_epoch

from .utils import override_api_settings


class MyToken(Token):
    token_type = 'test'
    lifetime = timedelta(days=1)


class TestToken(TestCase):
    def setUp(self):
        self.token = MyToken()
        self.token.set_exp(
            from_time=datetime(year=2000, month=1, day=1),
            lifetime=timedelta(seconds=0),
        )

    def test_init_no_token_type_or_lifetime(self):
        class MyTestToken(Token):
            pass

        with self.assertRaises(TokenError):
            MyTestToken()

        MyTestToken.token_type = 'test'

        with self.assertRaises(TokenError):
            MyTestToken()

        del MyTestToken.token_type
        MyTestToken.lifetime = timedelta(days=1)

        with self.assertRaises(TokenError):
            MyTestToken()

        MyTestToken.token_type = 'test'
        MyTestToken()

    def test_init_no_encoded_token_given(self):
        now = datetime(year=2000, month=1, day=1)

        with patch('rest_framework_simplejwt.tokens.datetime') as fake_datetime:
            fake_datetime.utcnow.return_value = now
            t = MyToken()

        self.assertEqual(t.current_time, now)
        self.assertIsNone(t.token)

        self.assertEqual(len(t.payload), 2)
        self.assertEqual(t.payload['exp'], datetime_to_epoch(now + MyToken.lifetime))
        self.assertEqual(t.payload[api_settings.TOKEN_TYPE_CLAIM], MyToken.token_type)

    def test_init_encoded_token_given(self):
        # Test successful instantiation
        original_now = datetime.utcnow()

        with patch('rest_framework_simplejwt.tokens.datetime') as fake_datetime:
            fake_datetime.utcnow.return_value = original_now
            good_token = MyToken()

        good_token['some_value'] = 'arst'
        encoded_good_token = str(good_token)

        now = datetime.utcnow()

        # Create new token from encoded token
        utcfromtimestamp = datetime.utcfromtimestamp
        with patch('rest_framework_simplejwt.tokens.datetime') as fake_datetime:
            fake_datetime.utcnow.return_value = now
            fake_datetime.utcfromtimestamp = utcfromtimestamp
            # Should raise no exception
            t = MyToken(encoded_good_token)

        # Should have expected properties
        self.assertEqual(t.current_time, now)
        self.assertEqual(t.token, encoded_good_token)

        self.assertEqual(len(t.payload), 3)
        self.assertEqual(t['some_value'], 'arst')
        self.assertEqual(t['exp'], datetime_to_epoch(original_now + MyToken.lifetime))
        self.assertEqual(t[api_settings.TOKEN_TYPE_CLAIM], MyToken.token_type)

        # Test backend rejects encoded token (expired or bad signature)
        payload = {'foo': 'bar'}
        payload['exp'] = datetime.utcnow() + timedelta(days=1)
        token = jwt.encode(payload, api_settings.SECRET_KEY, algorithm='HS256')
        payload['foo'] = 'baz'
        other_token = jwt.encode(payload, api_settings.SECRET_KEY, algorithm='HS256')

        incorrect_payload = other_token.rsplit('.', 1)[0]
        correct_sig = token.rsplit('.', 1)[-1]
        invalid_token = incorrect_payload + '.' + correct_sig

        with self.assertRaises(TokenError):
            t = MyToken(invalid_token)

        # Test encoded token has expired
        t = MyToken()
        t.set_exp(lifetime=-timedelta(seconds=1))

        with self.assertRaises(TokenError):
            MyToken(str(t))

        # Test encoded token has no token type
        t = MyToken()
        del t[api_settings.TOKEN_TYPE_CLAIM]

        with self.assertRaises(TokenError):
            MyToken(str(t))

        # Test encoded token has no wrong type
        t = MyToken()
        t[api_settings.TOKEN_TYPE_CLAIM] = 'wrong_type'

        with self.assertRaises(TokenError):
            MyToken(str(t))

    def test_str(self):
        # Should encode the given token
        encoded_token = str(self.token)

        # Token could be one of two depending on header dict ordering
        self.assertIn(
            encoded_token,
            (
                'eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ0b2tlbl90eXBlIjoidGVzdCIsImV4cCI6OTQ2Njg0ODAwfQ.pmyTEE6MqAUVhTUsXSIMhXnKtwhIXHeh6DTuQ5CfsFk',
                'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjk0NjY4NDgwMCwidG9rZW5fdHlwZSI6InRlc3QifQ.DAsRXwirDhvBd_SaiOEJowjCDpCq1hSEauAnW7mYDBA',
                'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoidGVzdCIsImV4cCI6OTQ2Njg0ODAwfQ.KhLI1M_Nkjjekz9g_mX4xYKmcinRuj-XkgLb59ncRwI',
                'eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJleHAiOjk0NjY4NDgwMCwidG9rZW5fdHlwZSI6InRlc3QifQ.X6MSEFhKEFtNKvSood0p7VFmKouyf8HSjeevPtd9a60'
            ),
        )

    def test_repr(self):
        self.assertEqual(repr(self.token), repr(self.token.payload))

    def test_getitem(self):
        self.assertEqual(self.token['exp'], self.token.payload['exp'])

    def test_setitem(self):
        self.token['test'] = 1234
        self.assertEqual(self.token.payload['test'], 1234)

    def test_delitem(self):
        self.token['test'] = 1234
        self.assertEqual(self.token.payload['test'], 1234)

        del self.token['test']
        self.assertNotIn('test', self.token)

    def test_contains(self):
        self.assertIn('exp', self.token)

    def test_set_exp(self):
        now = datetime(year=2000, month=1, day=1)

        token = MyToken()
        token.current_time = now

        # By default, should add 'exp' claim to token using `self.current_time`
        # and the TOKEN_LIFETIME setting
        token.set_exp()
        self.assertEqual(token['exp'], datetime_to_epoch(now + MyToken.lifetime))

        # Should allow overriding of beginning time, lifetime, and claim name
        token.set_exp(claim='refresh_exp', from_time=now, lifetime=timedelta(days=1))
        self.assertIn('refresh_exp', token)
        self.assertEqual(token['refresh_exp'], datetime_to_epoch(now + timedelta(days=1)))

    def test_check_exp(self):
        token = MyToken()

        # Should raise an exception if no claim of given kind
        with self.assertRaises(TokenError):
            token.check_exp('non_existent_claim')

        current_time = token.current_time
        lifetime = timedelta(days=1)
        exp = token.current_time + lifetime

        token.set_exp(lifetime=lifetime)

        # By default, checks 'exp' claim against `self.current_time`.  Should
        # raise an exception if claim has expired.
        token.current_time = exp
        with self.assertRaises(TokenError):
            token.check_exp()

        token.current_time = exp + timedelta(seconds=1)
        with self.assertRaises(TokenError):
            token.check_exp()

        # Otherwise, should raise no exception
        token.current_time = current_time
        token.check_exp()

        # Should allow specification of claim to be examined and timestamp to
        # compare against

        # Default claim
        with self.assertRaises(TokenError):
            token.check_exp(current_time=exp)

        token.set_exp('refresh_exp', lifetime=timedelta(days=1))

        # Default timestamp
        token.check_exp('refresh_exp')

        # Given claim and timestamp
        with self.assertRaises(TokenError):
            token.check_exp('refresh_exp', current_time=current_time + timedelta(days=1))
        with self.assertRaises(TokenError):
            token.check_exp('refresh_exp', current_time=current_time + timedelta(days=2))

    def test_for_user(self):
        username = 'test_user'
        user = User.objects.create_user(
            username=username,
            password='test_password',
        )

        token = MyToken.for_user(user)

        user_id = getattr(user, api_settings.USER_ID_FIELD)
        if not isinstance(user_id, int):
            user_id = text_type(user_id)

        self.assertEqual(token[api_settings.USER_ID_CLAIM], user_id)

        # Test with non-int user id
        with override_api_settings(USER_ID_FIELD='username'):
            token = MyToken.for_user(user)

        self.assertEqual(token[api_settings.USER_ID_CLAIM], username)


class TestSlidingToken(TestCase):
    def test_init(self):
        # Should set sliding refresh claim and token type claim
        token = SlidingToken()

        self.assertEqual(
            token[api_settings.SLIDING_REFRESH_EXP_CLAIM],
            datetime_to_epoch(token.current_time + api_settings.SLIDING_TOKEN_REFRESH_LIFETIME),
        )
        self.assertEqual(token[api_settings.TOKEN_TYPE_CLAIM], 'sliding')


class TestAccessToken(TestCase):
    def test_init(self):
        # Should set token type claim
        token = AccessToken()
        self.assertEqual(token[api_settings.TOKEN_TYPE_CLAIM], 'access')


class TestRefreshToken(TestCase):
    def test_init(self):
        # Should set token type claim
        token = RefreshToken()
        self.assertEqual(token[api_settings.TOKEN_TYPE_CLAIM], 'refresh')
