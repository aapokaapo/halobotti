
from typing import Iterable

from spnkr.models.profile import User
from spnkr.responses import JsonResponse
from spnkr.services.base import BaseService
from spnkr.xuid import unwrap_xuid, wrap_xuid

from pydantic import BaseModel
from functools import cached_property


class MyBaseService(BaseService):
    def __init__(self):
        super().__init__()
    
    async def _post(self, url: str, **kwargs) -> Response:
        """Make a GET request to `url` and return the response."""
        response = await self._session.post(url, **kwargs)
        if not hasattr(response, "from_cache"):
            # Only rate limit non-cached responses.
            await self._rate_limiter.acquire()
        response.raise_for_status()
        return response
        
        
_HOST = "https://profile.xboxlive.com"


class MyUser(BaseModel, frozen=True):
    """High-level information about an Xbox Live user.

    Attributes:
        xuid: Xbox user ID.
        gamertag: User's gamertag.
        gamerpic: URLs to different sizes of the user's gamerpic.
    """

    xuid: int
    gamertag: str


class MyProfileService(BaseService):

    async def get_xbl_users_by_id(self, xuids: Iterable[str | int]) -> JsonResponse[list[User]]:
        """Get user profiles for the given list of Xbox Live IDs.
    
        Args:
            xuids: The Xbox Live IDs of the players.
    
        Returns:
            A list of users.
    
        Raises:
            TypeError: If `xuids` is a `str` instead of an iterable of XUIDs.
        """
        if isinstance(xuids, str):
            raise TypeError("`xuids` must be an iterable of XUIDs, got `str`")
        url = f"{_HOST}/users/batch/profile/settings"
        params = {"userIds": [unwrap_xuid(x) for x in xuids], "settings": ["Gamertag"]}
        resp = await self._post(url, params=params)
        return JsonResponse(resp, lambda data: [MyUser(**u) for u in data])


class MyHaloInfiniteClient(HaloInfiniteClient):
    def __init__(
        self,
        session: "ClientSession | CachedSession",
        spartan_token: str,
        clearance_token: str,
        requests_per_second: int = 5,
    ) -> None:
        """Initialize a client for the Halo Infinite API.

        Args:
            session: The `aiohttp.ClientSession` to use. Support for caching is
                available via a `CachedSession` from `aiohttp-client-cache`.
            spartan_token: The spartan token used to authenticate with the API.
            clearance_token: The clearance token used to authenticate with the API.
            requests_per_second: The rate limit to use. Note that this rate
                limit is enforced per service, not globally. Defaults to 5
                requests per second.
        """
        super().__init__(session, spartan_token, clearance_token, requests_per_second)
        
        @cached_property
        def my_profile(self) -> MyProfileService:
            """Profile data service. Get user data, such as XUIDs/gamertags."""
            return MyProfileService(self._session, self._requests_per_second)

        
