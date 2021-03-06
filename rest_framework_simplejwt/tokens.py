from __future__ import unicode_literals

from uuid import UUID, uuid4

from django.conf import settings
from django.utils.six import python_2_unicode_compatible, text_type
from django.utils.translation import ugettext_lazy as _

from .exceptions import TokenBackendError, TokenError
from .settings import api_settings
from .token_blacklist.models import BlacklistedToken, OutstandingToken
from .utils import (
    aware_utcnow, datetime_from_epoch, datetime_to_epoch, format_lazy
)


@python_2_unicode_compatible
class Token(object):
    """
    A class which validates and wraps an existing JWT or can be used to build a
    new JWT.
    """
    token_type = None
    lifetime = None

    def __init__(self, token=None):
        """
        !!!! IMPORTANT !!!! MUST raise a TokenError with a user-facing error
        message if the given token is invalid, expired, or otherwise not safe
        to use.
        """
        if self.token_type is None or self.lifetime is None:
            raise TokenError(_('Cannot create token with no type or lifetime'))

        self.token = token
        self.current_time = aware_utcnow()

        # Set up token
        if token is not None:
            # An encoded token was provided
            from .state import token_backend

            # Ensure token and signature are valid
            try:
                self.payload = token_backend.decode(token)
            except TokenBackendError:
                raise TokenError(_('Token is invalid or expired'))

            # According to RFC 7519, the "exp" claim is OPTIONAL
            # (https://tools.ietf.org/html/rfc7519#section-4.1.4).  As a more
            # correct behavior for authorization tokens, we require an "exp"
            # claim.  We don't want any zombie tokens walking around.
            self.check_exp()

            # Ensure token type claim is present and has correct value
            try:
                token_type = self.payload[api_settings.TOKEN_TYPE_CLAIM]
            except KeyError:
                raise TokenError(_('Token has no type'))

            if self.token_type != token_type:
                raise TokenError(_('Token has wrong type'))

            # Ensure token id is present
            if 'jti' not in self.payload:
                raise TokenError(_('Token has no id'))

        else:
            # This is a new token.  Skip all the validation steps.  Set token
            # type and token id claims.
            self.payload = {
                api_settings.TOKEN_TYPE_CLAIM: self.token_type,
                'jti': uuid4().hex,
            }

            # Set "exp" claim with default value
            self.set_exp(from_time=self.current_time, lifetime=self.lifetime)

    def __repr__(self):
        return repr(self.payload)

    def __getitem__(self, key):
        return self.payload[key]

    def __setitem__(self, key, value):
        self.payload[key] = value

    def __delitem__(self, key):
        del self.payload[key]

    def __contains__(self, key):
        return key in self.payload

    def __str__(self):
        """
        Signs and returns a token as a base64 encoded string.
        """
        from .state import token_backend

        return token_backend.encode(self.payload)

    def set_exp(self, claim='exp', from_time=None, lifetime=None):
        """
        Updates the expiration time of a token.
        """
        if from_time is None:
            from_time = self.current_time

        if lifetime is None:
            lifetime = self.lifetime

        self.payload[claim] = datetime_to_epoch(from_time + lifetime)

    def check_exp(self, claim='exp', current_time=None):
        """
        Checks whether a timestamp value in the given claim has passed (since
        the given datetime value in `current_time`).  Raises a TokenError with
        a user-facing error message if so.
        """
        if current_time is None:
            current_time = self.current_time

        try:
            claim_value = self.payload[claim]
        except KeyError:
            raise TokenError(format_lazy(_("Token has no '{}' claim"), claim))

        claim_time = datetime_from_epoch(claim_value)
        if claim_time <= current_time:
            raise TokenError(format_lazy(_("Token '{}' claim has expired"), claim))

    @classmethod
    def for_user(cls, user):
        """
        Returns an authorization token for the given user that will be provided
        after authenticating the user's credentials.
        """
        user_id = getattr(user, api_settings.USER_ID_FIELD)
        if not isinstance(user_id, int):
            user_id = text_type(user_id)

        token = cls()
        token[api_settings.USER_ID_CLAIM] = user_id

        return token


class BlacklistMixin(object):
    """
    If the `rest_framework_simplejwt.token_blacklist` app was configured to be
    used, tokens created from `BlacklistMixin` subclasses will insert
    themselves into an outstanding token list and also check for their
    membership in a token blacklist.
    """
    if 'rest_framework_simplejwt.token_blacklist' in settings.INSTALLED_APPS:
        def __init__(self, *args, **kwargs):
            super(BlacklistMixin, self).__init__(*args, **kwargs)

            if self.token is not None:
                self.check_blacklist()

        def check_blacklist(self):
            """
            Check if this token is present in the token blacklist.  Raise
            `TokenError` if so.
            """
            jti = UUID(hex=self.payload['jti'])

            try:
                BlacklistedToken.objects.get(token__jti=jti)
                raise TokenError(_('Token is blacklisted'))
            except BlacklistedToken.DoesNotExist:
                pass

        @classmethod
        def for_user(cls, user):
            """
            Add this token to the outstanding token list.
            """
            token = super(BlacklistMixin, cls).for_user(user)

            exp = token['exp']
            jti = token['jti']

            OutstandingToken.objects.create(
                user=user,
                jti=UUID(hex=jti),
                token=str(token),
                created_at=token.current_time,
                expires_at=datetime_from_epoch(exp),
            )

            return token


class SlidingToken(BlacklistMixin, Token):
    token_type = 'sliding'
    lifetime = api_settings.SLIDING_TOKEN_LIFETIME

    def __init__(self, *args, **kwargs):
        super(SlidingToken, self).__init__(*args, **kwargs)

        if self.token is None:
            # Set sliding refresh expiration claim if new token
            self.set_exp(
                api_settings.SLIDING_TOKEN_REFRESH_EXP_CLAIM,
                from_time=self.current_time,
                lifetime=api_settings.SLIDING_TOKEN_REFRESH_LIFETIME,
            )


class RefreshToken(BlacklistMixin, Token):
    token_type = 'refresh'
    lifetime = api_settings.REFRESH_TOKEN_LIFETIME
    no_copy_claims = (api_settings.TOKEN_TYPE_CLAIM, 'exp', 'jti')

    @property
    def access_token(self):
        """
        Returns an access token created from this refresh token.  Copies all
        claims present in this refresh token to the new access token except
        those claims listed in the `no_copy_claims` attribute.
        """
        access = AccessToken()

        # Use instantiation time of refresh token as relative timestamp for
        # access token "exp" claim.  This ensures that both a refresh and
        # access token expire relative to the same time if they are created as
        # a pair.
        access.set_exp(from_time=self.current_time)

        no_copy = self.no_copy_claims
        for claim, value in self.payload.items():
            if claim in no_copy:
                continue
            access[claim] = value

        return access


class AccessToken(Token):
    token_type = 'access'
    lifetime = api_settings.ACCESS_TOKEN_LIFETIME
