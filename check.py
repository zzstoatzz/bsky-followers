# /// script
# dependencies = [
#   "atproto",
#   "prefect@git+https://github.com/prefecthq/prefect.git",
# ]
# ///

from datetime import datetime
from pathlib import Path
from typing import TypedDict

from atproto import Client
from prefect import flow, task
from pydantic import Field, model_validator
from pydantic_core import from_json, to_json
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bsky_handle: str = Field(default=...)
    bsky_password: str = Field(default=...)
    follower_cache: Path | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def check_credentials(cls, values: dict):
        if not (values.get("bsky_handle") and values.get("bsky_password")):
            raise ValueError("Must set BSKY_HANDLE and BSKY_PASSWORD.")
        return values
    
    @model_validator(mode="after")
    def set_cache_path(self):
        if self.follower_cache is None:
            username = self.bsky_handle.replace("@", "").replace(".", "_")
            cache_dir = Path("~/.bsky").expanduser()
            cache_dir.mkdir(exist_ok=True)
            self.follower_cache = cache_dir / f"{username}_followers.json"
        return self


class FollowerState(TypedDict):
    followers: set[str]
    timestamp: str | None


@task(persist_result=False)
def fetch_followers_from_atproto(settings: Settings) -> set[str]:
    client = Client()
    client.login(settings.bsky_handle, settings.bsky_password)
    assert client.me, "Login failed"
    fs, cursor = set(), None
    while True:
        assert client.me, "client.me should be set"
        r = client.get_followers(client.me.handle, cursor=cursor)
        fs |= {f.handle for f in r.followers}
        if not r.cursor:
            break
        cursor = r.cursor
    bsky_count = client.get_profile(client.me.handle).followers_count
    print(f"App counted: {bsky_count}, Cache counted: {len(fs)}")
    if bsky_count != len(fs):
        print("Discrepancy detected (disabled accounts / slow indexing etc)")
    return fs


@task
def load_known_followers_from_cache(settings: Settings) -> FollowerState:
    try:
        data = from_json(settings.follower_cache.read_bytes())
        return FollowerState(
            followers=set(data["followers"]), timestamp=data["timestamp"]
        )
    except FileNotFoundError:
        return FollowerState(followers=set(), timestamp=None)


@task
def save_updated_followers_to_cache(fs: set[str], settings: Settings):
    settings.follower_cache.write_bytes(
        to_json(
            {"followers": list(fs), "timestamp": datetime.now().isoformat()},
            indent=2,
        )
    )


@flow(log_prints=True)
def check_bsky_followers(settings: Settings):
    current = fetch_followers_from_atproto(settings)
    known = load_known_followers_from_cache(settings, wait_for=[current])
    new, lost = current - known["followers"], known["followers"] - current

    if not known["followers"]:
        print(f"👋 welcome! you have {len(current)} followers.")
    elif new or lost:
        print(f"🙂 welcome back! since {known['timestamp']} ↑{len(new)} ↓{len(lost)}.")
        if new:
            print(f"🤗 new followers:\n\t- {'\n\t- '.join(new)}")
        if lost:
            print(f"🥲 lost followers:\n\t- {'\n\t- '.join(lost)}")
    else:
        print(f"🙂 welcome back! no changes since {known['timestamp']}")

    save_updated_followers_to_cache(current, settings, wait_for=[known])


if __name__ == "__main__":
    check_bsky_followers(Settings())
