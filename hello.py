# /// script
# dependencies = [
#     "atproto",
#     "prefect@git+https://github.com/PrefectHQ/prefect.git",
# ]
# ///


from datetime import datetime
from pathlib import Path
from typing import NamedTuple, TypedDict

from atproto import Client
from prefect import flow, task
from prefect.cache_policies import NONE
from pydantic import Field
from pydantic_core import from_json, to_json
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    bsky_handle: str = Field(default=...)
    bsky_password: str = Field(default=...)


class Follower(TypedDict):
    handle: str
    did: str | None
    display_name: str | None


class FollowerState(NamedTuple):
    followers: set[str]
    timestamp: str | None


@task(cache_policy=NONE)
def get_followers(client: Client, handle: str) -> list[Follower]:
    """Fetch all followers for a given handle."""
    followers: list[Follower] = []
    cursor: str | None = None

    while True:
        response = client.get_followers(actor=handle, cursor=cursor)
        followers.extend(
            {"handle": f.handle, "did": f.did, "display_name": f.display_name}
            for f in response.followers
        )
        if not response.cursor:
            break
        cursor = response.cursor
    assert followers, "No followers found"
    return followers


@task
def load_previous_followers(path: Path = Path("followers.json")) -> FollowerState:
    """Load followers from the last run."""
    try:
        data = from_json(path.read_bytes())
        return FollowerState(set(data["followers"]), data["timestamp"])
    except FileNotFoundError:
        return FollowerState(set(), None)


@task
def save_current_followers(
    followers: set[str], path: Path = Path("followers.json")
) -> None:
    """Save current followers to file."""
    path.write_bytes(
        to_json(
            {"followers": list(followers), "timestamp": datetime.now().isoformat()},
            indent=2,
        )
    )


@flow(log_prints=True)
def check_bsky_followers(bsky_handle: str, bsky_password: str) -> None:
    client = Client()
    client.login(bsky_handle, bsky_password)
    assert client.me is not None, "Failed to login"

    current_followers_full = get_followers(client, client.me.handle)
    current_followers = {f["handle"] for f in current_followers_full}
    app_count = client.get_profile(client.me.handle).followers_count

    print(f"App shows: {app_count} followers")
    print(f"Script found: {len(current_followers)} active followers")

    if app_count != len(current_followers):
        print("\nDiscrepancy detected!")
        print(
            "this could be due to deleted/suspended/private accounts, slow indexing, e.g"
        )

    previous_followers, last_check = load_previous_followers(
        wait_for=[current_followers_full]
    )
    new_followers = current_followers - previous_followers
    unfollowers = previous_followers - current_followers

    if not previous_followers:
        print(f"First run! You have {len(current_followers)} followers")
    elif new_followers or unfollowers:
        print(f"\nChanges since {last_check}:")
        if new_followers:
            print("\nNew followers:", *[f"+ {f}" for f in new_followers], sep="\n")
        if unfollowers:
            print("\nUnfollowers:", *[f"- {f}" for f in unfollowers], sep="\n")
    else:
        print("No changes in followers")

    save_current_followers(current_followers, wait_for=[previous_followers])


if __name__ == "__main__":
    settings = Settings()
    check_bsky_followers(settings.bsky_handle, settings.bsky_password)
