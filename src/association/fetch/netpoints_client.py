"""Client for ESPN Analytics' per-DATE NetPoints files (player_box/team_box
for every game played on a given date, in one request).

Unlike net_points_player/net_points_team's flat season files (a fully public
S3 bucket, no auth needed - see endpoints.py), this bucket rejects unsigned
requests with 403 AccessDenied (confirmed live). The espnanalytics.com site
itself gets around this by exchanging a public AWS Cognito "unauthenticated
identity" pool ID - embedded in its own client-side JS, the standard way
anonymous browsers are meant to use these pools - for temporary, SigV4-
signing AWS credentials, then makes a normal S3 GetObject call. This
replicates exactly that flow: the same two Cognito API calls any visitor's
browser makes, followed by a read-only S3 GetObject.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import boto3
from botocore.exceptions import ClientError

log: logging.Logger = logging.getLogger("association.fetch.netpoints_client")

REGION = "us-east-1"
IDENTITY_POOL_ID = "us-east-1:7b073343-561b-4a8f-bf2a-765958c3aaaa"
BUCKET = "espnsportsanalytics.com"


class NetPointsDailyClient:
    """Reader for per-game NetPoints, which live in a private S3 prefix.

    Access needs a Cognito credential exchange - unauthenticated in the sense that
    credentials are issued to anyone who asks, with no account - so the client is
    built lazily and only when a date is actually fetched.
    """

    def __init__(self) -> None:
        self._s3: Any = None

    def _client(self) -> Any:
        # Built lazily (not in __init__) so constructing a Pipeline never makes
        # a network call on its own - only actually fetching a date does.
        if self._s3 is None:
            cognito = boto3.client("cognito-identity", region_name=REGION)
            identity_id = cognito.get_id(IdentityPoolId=IDENTITY_POOL_ID)["IdentityId"]
            creds = cognito.get_credentials_for_identity(IdentityId=identity_id)["Credentials"]
            self._s3 = boto3.client(
                "s3",
                region_name=REGION,
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretKey"],
                aws_session_token=creds["SessionToken"],
            )
        return self._s3

    def get_daily(self, date: str, season_folder: int) -> dict[str, Any] | None:
        """date: 'YYYY-MM-DD'. season_folder: the NetPoints (start-year)
        season this date falls in - the bucket is keyed by both. Returns None
        if nothing was published for that date (no games, or before NetPoints'
        2018-10-16 data floor)."""
        key = f"NBA/netpts/{season_folder}/{date}.json"
        try:
            obj = self._client().get_object(Bucket=BUCKET, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "AccessDenied"):
                # AccessDenied (not just NoSuchKey) is what a missing key
                # actually returns here - this identity has GetObject but not
                # ListBucket, and S3 reports that combination as AccessDenied
                # for a key that doesn't exist rather than 404 (confirmed live).
                return None
            raise
        body = obj["Body"].read()
        if not body:
            return None
        result: dict[str, Any] = json.loads(body)
        return result
